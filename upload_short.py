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
    print("\n" + "=" * 50)
    print("SUCCESS: Video uploaded to YouTube!")
    print(f"Video ID : {video_id}")
    print(f"Short URL: https://youtube.com/shorts/{video_id}")
    print("=" * 50)
    return response


if __name__ == "__main__":
    test_video = "final_short.mp4"
    if not os.path.exists(test_video):
        print(f"Error: Test video file '{test_video}' not found.")
        sys.exit(1)

    upload_short(
        video_file=test_video,
        title="Automated Short Test #shorts",
        description="Testing automated video clipping upload pipeline.\n\n#shorts #testing",
        tags=["shorts", "test", "automation"],
        privacy_status="unlisted",
    )
