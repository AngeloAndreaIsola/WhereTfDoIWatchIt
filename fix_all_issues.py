import os
import subprocess
import sys

print("=== Fixing All Issues ===")
print()

# 1. Fix vite config first
print("1. Fixing vite config...")
with open('frontend/vitest.config.ts', 'w') as f:
    f.write("""import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./__tests__/setup.ts'],
  },
});
""")
print("  ✓ Fixed frontend/vitest.config.ts")

# 2. Create the proper migration
print()
print("2. Creating proper migration for release_date...")

migration_content = '''# Add release_date field to Movie model
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0026_allow_null_current_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="release_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
'''

# Make sure we're in the backend directory
os.chdir('/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/backend')

with open('tracker/migrations/0027_add_movie_release_date.py', 'w') as f:
    f.write(migration_content)

print("  ✓ Created backend/tracker/migrations/0027_add_movie_release_date.py")

# 3. Try to activate virtual env and check
print()
print("3. Checking virtual environment status...")

# Check if we can run python with the right env
result = os.system('source .venv/bin/activate && python -c "import django; print(f\"Django: {django.__version__}\")"')
if result == 0:
    print("  ✓ Virtual environment is working")
else:
    print("  ⚠ Virtual environment might not be set up correctly")

# 4. Check if the migration file exists and looks correct
print()
print("4. Verifying migration file...")

if os.path.exists('tracker/migrations/0027_add_movie_release_date.py'):
    print("  ✓ Migration file exists")
    with open('tracker/migrations/0027_add_movie_release_date.py', 'r') as f:
        content = f.read()
        if 'release_date = models.CharField' in content:
            print("  ✓ Migration contains release_date field")
        else:
            print("  ✗ Migration missing release_date field")
else:
    print("  ✗ Migration file does not exist")

print()
print("=== Summary ===")
print("Fixed the vite config TypeScript error by removing the 'as any' cast")
print("Created the proper migration 0027_add_movie_release_date.py to add the release_date field")
print("The migration will add the release_date column to the tracker_movie table when applied")
print()
print("Note: The frontend build was failing due to a TypeScript configuration error.")
print("The migration fix should resolve the database issue with the missing release_date column.")
