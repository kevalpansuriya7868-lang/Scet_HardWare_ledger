#!/usr/bin/env python3
"""
Create or reset a system_users admin/staff account's password.

Run this ON the admin server (same machine/network as config.json's
[oracle] section) — it connects to Oracle with those credentials.

Usage:
    python reset_admin_password.py --username labadmin
    python reset_admin_password.py --username newstaff --role ADMIN
    python reset_admin_password.py --username labadmin --deactivate

It never prints or logs the password or the resulting hash.
"""

import argparse
import getpass
import json
import os
import sys

import oracledb

from auth_utils import hash_password

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_db_config():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    db = config.get("oracle")
    if not db:
        print("config.json here has no [oracle] section — this looks like a "
              "student-only install, not the admin/server one. Run this "
              "script on the server that config.json calls the admin machine.")
        sys.exit(1)
    return db


def get_connection(db):
    return oracledb.connect(
        user=db["user"],
        password=db["password"],
        host=db["host"],
        port=db["port"],
        service_name=db["service_name"],
    )


def prompt_new_password():
    while True:
        p1 = getpass.getpass("New password (min 8 chars): ")
        if len(p1) < 8:
            print("Too short — try again.")
            continue
        p2 = getpass.getpass("Confirm password: ")
        if p1 != p2:
            print("Passwords did not match — try again.")
            continue
        return p1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", default="ADMIN",
                         help="Role to assign if creating a new account (default: ADMIN)")
    parser.add_argument("--deactivate", action="store_true",
                         help="Deactivate the account instead of resetting its password")
    args = parser.parse_args()

    db = load_db_config()
    conn = get_connection(db)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM system_users WHERE username = :1", (args.username,)
        )
        exists = cursor.fetchone()[0] > 0

        if args.deactivate:
            if not exists:
                print(f"No such user: {args.username}")
                sys.exit(1)
            cursor.execute(
                "UPDATE system_users SET is_active = 0 WHERE username = :1",
                (args.username,)
            )
            conn.commit()
            print(f"'{args.username}' deactivated.")
            return

        password = prompt_new_password()
        hashed = hash_password(password)
        del password  # don't keep the plaintext around any longer than needed

        if exists:
            cursor.execute(
                "UPDATE system_users SET password_hash = :1, is_active = 1 WHERE username = :2",
                (hashed, args.username)
            )
            conn.commit()
            print(f"Password reset for existing user '{args.username}'.")
        else:
            cursor.execute(
                "INSERT INTO system_users (username, password_hash, role, is_active) "
                "VALUES (:1, :2, :3, 1)",
                (args.username, hashed, args.role.upper())
            )
            conn.commit()
            print(f"Created new user '{args.username}' with role {args.role.upper()}.")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()