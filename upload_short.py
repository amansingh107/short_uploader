import os
import sys
import time
import http.client
import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
RETRIABLE_EXCEPTIONS = (
    httplib2.HttpLib2Error,
    IOError,
    http.client.NotConnected,
    http.client.IncompleteRead,
    http.client.ImproperConnectionState,
    http.client.CannotSendRequest,
    http.client.CannotSendHeader,
    http.client.ResponseNotReady,
    http.client.BadStatusLine,
)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
MAX_RETRIES = 10


def get_authenticated_service(client_secret_file="client_secret.json", token_file="token.json"):
    creds = None
    if os.path.exists(token_file):
        print(f"Loading cached credentials from '{token_file}'...")
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                raise RuntimeError(
                    f"OAuth credentials in '{token_file}' are invalid or missing, and interactive login cannot run in headless CI. "
                    f"Please re-authenticate locally using 'python auth_account.py --name <account>' and update the GitHub secret."
                )
            if not os.path.exists(client_secret_file):
                raise FileNotFoundError(
                    f"'{client_secret_file}' not found. Please provide OAuth client credentials."
                )
            print(f"Starting OAuth flow using '{client_secret_file}'...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as token:
            token.write(creds.to_json())
            print(f"Saved credentials to '{token_file}'.")

    return build("youtube", "v3", credentials=creds)


def upload_short(
    video_file: str,
    title: str,
    description: str,
    tags: list[str] = None,
    privacy_status: str = "unlisted",
    category_id: str = "22",
    made_for_kids: bool = False,
    token_file: str = "token.json",
    client_secret_file: str = "client_secret.json",
):
    if not os.path.exists(video_file):
        raise FileNotFoundError(f"Video file '{video_file}' not found.")

    if "#shorts" not in title.lower() and "#shorts" not in description.lower():
        title = f"{title} #shorts"

    youtube = get_authenticated_service(client_secret_file=client_secret_file, token_file=token_file)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    media = MediaFileUpload(
        video_file,
        chunksize=1024 * 1024 * 4,  # 4MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    file_size_mb = os.path.getsize(video_file) / (1024 * 1024)
    print(f"\nStarting upload: {video_file} ({file_size_mb:.2f} MB)")
    print(f"Title: {title}")
    print(f"Privacy: {privacy_status}")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"Upload progress: {int(status.progress() * 100)}%")
            retry_count = 0
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error_msg = f"Retriable HTTP error {e.resp.status}: {e}"
            else:
                raise
            if retry_count < MAX_RETRIES:
                retry_count += 1
                sleep_time = 2 ** retry_count
                print(f"{error_msg}\nRetrying in {sleep_time}s (Attempt {retry_count}/{MAX_RETRIES})...")
                time.sleep(sleep_time)
            else:
                raise RuntimeError("Maximum retries reached. Upload failed.")
        except RETRIABLE_EXCEPTIONS as e:
            error_msg = f"Retriable network error: {e}"
            if retry_count < MAX_RETRIES:
                retry_count += 1
                sleep_time = 2 ** retry_count
                print(f"{error_msg}\nRetrying in {sleep_time}s (Attempt {retry_count}/{MAX_RETRIES})...")
                time.sleep(sleep_time)
            else:
                raise RuntimeError("Maximum retries reached. Upload failed.")

    video_id = response["id"]
    status_info = response.get("status", {})
    actual_privacy = status_info.get("privacyStatus", privacy_status)
    upload_status = status_info.get("uploadStatus", "uploaded")

    print("\n" + "=" * 55)
    print("SUCCESS: Video uploaded to YouTube!")
    print(f"Video ID        : {video_id}")
    print(f"Requested Status: {privacy_status}")
    print(f"Actual Status   : {actual_privacy} (Upload Status: {upload_status})")
    print(f"Short URL       : https://youtube.com/shorts/{video_id}")
    print(f"Studio Edit URL : https://studio.youtube.com/video/{video_id}/edit")
    if actual_privacy.lower() != privacy_status.lower():
        print(f"\n[NOTE] YouTube set visibility to '{actual_privacy}'.")
        print("This happens because the Google Cloud project is in 'Testing' mode.")
        print("To make it public, visit YouTube Studio and change visibility to Public.")
    print("=" * 55)
    return response


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Upload a video / Short directly to YouTube")
    parser.add_argument("--video", "-v", required=True, help="Path to the video MP4 file to upload")
    parser.add_argument("--title", "-t", default="Uploaded Short #shorts", help="Video Title (ends with #shorts)")
    parser.add_argument("--description", "-d", default="Check out this short!\n\n#shorts #trending #viral", help="Video Description")
    parser.add_argument("--privacy", "-p", default="unlisted", choices=["public", "unlisted", "private"], help="Privacy status (default: unlisted)")
    parser.add_argument("--account", "-a", default="account1", help="Target account: 'account1', 'account2', or path to token JSON")
    parser.add_argument("--secret", "-s", default=None, help="Path to client secret JSON file (optional)")
    parser.add_argument("--tags", nargs="*", default=["shorts", "viral", "trending"], help="Tags list")

    args = parser.parse_args()

    # Resolve token path
    token_candidate = args.account
    if not os.path.exists(token_candidate):
        if os.path.exists(os.path.join("tokens", token_candidate)):
            token_candidate = os.path.join("tokens", token_candidate)
        elif os.path.exists(os.path.join("tokens", f"{token_candidate}.json")):
            token_candidate = os.path.join("tokens", f"{token_candidate}.json")
        elif os.path.exists(f"{token_candidate}.json"):
            token_candidate = f"{token_candidate}.json"

    if not os.path.exists(token_candidate):
        print(f"Error: Token file '{token_candidate}' not found.")
        sys.exit(1)

    acc_base = os.path.splitext(os.path.basename(token_candidate))[0]

    # Resolve client secret path
    secret_path = args.secret
    if not secret_path:
        secret_candidates = [
            f"client_secret_{acc_base}.json",
            f"client_{acc_base}.json",
            "client_secret.json",
        ]
        secret_path = next((s for s in secret_candidates if os.path.exists(s)), "client_secret.json")

    print(f"Target Account: [{acc_base}] (Token: {token_candidate}, Secret: {secret_path})")
    upload_short(
        video_file=args.video,
        title=args.title,
        description=args.description,
        tags=args.tags,
        privacy_status=args.privacy,
        token_file=token_candidate,
        client_secret_file=secret_path,
    )
