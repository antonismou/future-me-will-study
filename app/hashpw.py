"""Δημιουργεί bcrypt hash για το ADMIN_PASSWORD_HASH.

    docker compose run --rm app python -m app.hashpw
"""
import getpass
import sys

import bcrypt


def main() -> None:
    pw = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Νέος κωδικός: ")
    if len(sys.argv) == 1 and pw != getpass.getpass("Ξανά: "):
        sys.exit("Οι κωδικοί δεν ταιριάζουν.")
    if len(pw) < 10:
        sys.exit("Χρησιμοποίησε τουλάχιστον 10 χαρακτήρες.")
    h = bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
    # Μονά εισαγωγικά: το docker compose δεν κάνει interpolation των $ μέσα σε αυτά.
    print(f"ADMIN_PASSWORD_HASH='{h}'")


if __name__ == "__main__":
    main()
