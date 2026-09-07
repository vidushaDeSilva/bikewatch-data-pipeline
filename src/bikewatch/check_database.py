import os

import psycopg
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    current_database(),
                    current_user,
                    current_timestamp
                """
            )

            database, user, timestamp = cursor.fetchone()

    print(f"Database: {database}")
    print(f"User: {user}")
    print(f"Connected at: {timestamp}")


if __name__ == "__main__":
    main()