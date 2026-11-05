import os
import sys
import argparse
from upload_short import get_authenticated_service

# Force UTF-8 stdout encoding on Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def find_client_secret(account_name: str, explicit_secret: str = None) -> str:
    """Resolve the client secret file for a given account name."""
    if explicit_secret and os.path.exists(explicit_secret):
        return explicit_secret

    candidates = [
        f"client_secret_{account_name}.json",
        f"client_{account_name}.json",
        f"client_secret.json",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return explicit_secret or f"client_secret_{account_name}.json"


def authenticate_account(account_name: str, client_secret_file: str = None):
    """Authenticate a specific YouTube account with its own client secret."""
    tokens_dir = "tokens"
    os.makedirs(tokens_dir, exist_ok=True)
    token_file = os.path.join(tokens_dir, f"{account_name}.json")

    secret_file = find_client_secret(account_name, client_secret_file)

    print("=" * 65)
    print(f"🔑 AUTHENTICATING YOUTUBE ACCOUNT: '{account_name}'")
    print(f"   Using Client Secret : {secret_file}")
    print(f"   Target Token File   : {token_file}")
    print("=" * 65)

    if not os.path.exists(secret_file):
        print(f"\n[ERROR] Client secret file '{secret_file}' not found!")
        print(f"Please place your downloaded OAuth JSON as '{secret_file}' in this folder.")
        sys.exit(1)

    try:
        service = get_authenticated_service(
            client_secret_file=secret_file,
            token_file=token_file,
        )
        print("\n[SUCCESS] Authentication succeeded!")
        print(f"Credentials saved to: '{token_file}'")
        print("=" * 65)
    except Exception as e:
        print(f"\n[ERROR] Authentication failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authenticate YouTube account with separate client secrets")
    parser.add_argument("--name", required=True, help="Account label (e.g., account1, account2)")
    parser.add_argument("--secret", default=None, help="Path to this account's client secret JSON file")

    args = parser.parse_args()
    authenticate_account(account_name=args.name, client_secret_file=args.secret)
