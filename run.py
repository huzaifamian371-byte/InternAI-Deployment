import os
import click
from app import create_app
from seed import seed_database

app = create_app(os.getenv("FLASK_ENV", "development"))


@app.cli.command("seed-db")
def seed_db_command():
    """Seed the database with initial master skills catalog and admin account."""
    click.echo("Seeding master skills catalog and admin user...")
    seed_database()
    click.echo("Database seeding completed.")


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", 5000))
    app.run(host=host, port=port, debug=True)
