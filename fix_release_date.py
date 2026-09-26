import os
import sys

# Change to backend directory where manage.py is located
os.chdir('/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/backend')

# Set the Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tracker.settings')

print("=== Fixing release_date migration issue ===")
print()

# Step 1: Check current migration status
print("Step 1: Checking current migration status...")
result = os.system('python manage.py showmigrations tracker')
if result != 0:
    print(f"Warning: showmigrations failed with code {result}")
print()

# Step 2: Make sure we have the proper migrations
print("Step 2: Checking migration files...")

# Check if 0028 exists
if not os.path.exists('tracker/migrations/0028_movie_release_date.py'):
    print("Creating migration 0028_movie_release_date.py...")
    with open('tracker/migrations/0028_movie_release_date.py', 'w') as f:
        f.write('''# Generated migration for release_date field
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0027_movie_first_air_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="release_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
''')

# Check if 0027 exists
if not os.path.exists('tracker/migrations/0027_movie_first_air_date.py'):
    print("Creating migration 0027_movie_first_air_date.py...")
    with open('tracker/migrations/0027_movie_first_air_date.py', 'w') as f:
        f.write('''# Generated migration for first_air_date field
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0026_allow_null_current_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="first_air_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
''')

print("Done creating migrations.")
print()

# Step 3: Apply the 0027 migration first
print("Step 3: Applying migration 0027...")
result = os.system('python manage.py migrate tracker 0027')
if result == 0:
    print("✓ Migration 0027 applied successfully")
else:
    print(f"✗ Migration 0027 failed with code {result}")
    print("This is expected if 0027 already applied")

# Step 4: Apply the 0028 migration
print()
print("Step 4: Applying migration 0028...")
result = os.system('python manage.py migrate tracker 0028')
if result == 0:
    print("✓ Migration 0028 applied successfully")
else:
    print(f"✗ Migration 0028 failed with code {result}")

print()
print("Step 5: Verifying migrations...")
result = os.system('python manage.py showmigrations tracker')

print()
print("=== Verification ===")

# Check database state using shell
result = os.system('python manage.py shell -c '
                   '"'
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute(\"SELECT column_name FROM information_schema.columns WHERE table_schema = 'tracker' AND table_name = 'movie'\")
    columns = [row[0] for row in cursor.fetchall()]
    
    print(f"Total columns: {len(columns)}")
    for col in sorted(columns):
        print(f"  {col}")
    
    # Check for critical columns
    for required in [\"release_date\", \"first_air_date\"]:
        cursor.execute(f\"SELECT column_name FROM information_schema.columns WHERE table_schema = 'tracker' AND table_name = 'movie' AND column_name = '{required}'\")
        if cursor.fetchone():
            print(f\"✓ {required} column EXISTS in database\")
        else:
            print(f\"✗ {required} column DOES NOT EXIST in database\")
'
                   '"')

print()
print("=== Fix complete ===")
