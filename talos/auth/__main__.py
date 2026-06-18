"""Bootstrap CLI — creates the initial user on a fresh air-gapped install.

Usage:
    python -m talos.auth add-user <username>

Prompts for password (hidden input), hashes with argon2id, inserts into the
users table. See docs/install.md for the full installation runbook.
"""
import getpass
import sys

from talos.auth.users import add_user


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "add-user":
        print("Usage: python -m talos.auth add-user <username>", file=sys.stderr)
        sys.exit(1)
    username = sys.argv[2]
    password = getpass.getpass(f"Password for {username!r}: ")
    if not password:
        print("Error: password cannot be empty.", file=sys.stderr)
        sys.exit(1)
    add_user(username, password)
    print(f"User {username!r} created.")


if __name__ == "__main__":
    main()
