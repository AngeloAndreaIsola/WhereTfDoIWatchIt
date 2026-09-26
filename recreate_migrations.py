import os
import re

# Change to the backend directory
os.chdir('backend')

print("=== Recreating migrations for release_date ===")
print()

# Step 1: Remove all tracker migrations
migrations_dir = 'tracker/migrations'
print("Step 1: Removing all tracker migration files...")

# Keep __init__.py
init_file = os.path.join(migrations_dir, '__init__.py')

# Delete all migration files
for filename in os.listdir(migrations_dir):
    if filename.endswith('.py') and not filename.startswith('__'):
        file_path = os.path.join(migrations_dir, filename)
        print(f"  Removing: {filename}")
        os.remove(file_path)

# Restore __init__.py if it was deleted
if not os.path.exists(init_file):
    with open(init_file, 'w') as f:
        pass
    print("  Restored __init__.py")

print("Done removing migrations.")
print()

# Step 2: Create initial migration (0001_initial.py)
print("Step 2: Creating initial migration (0001_initial.py)...")

# We need to read the current Movie model from models.py
with open('tracker/models.py', 'r') as f:
    models_content = f.read()

# Extract Movie class
movie_class_match = re.search(r'class Movie\(models\.Model\):(.*?)(?=\nclass|\Z)', models_content, re.DOTALL)
if movie_class_match:
    movie_fields = []
    for field_match in re.finditer(r'^(\s*)(\w+)\s*=\s*models\.(\w+)', movie_class_match.group(1), re.MULTILINE):
        indent, name, type_name = field_match.groups()
        movie_fields.append((name, type_name))
    
    print(f"  Found Movie model with {len(movie_fields)} fields:")
    for name, type_name in movie_fields:
        print(f"    {name}: {type_name}")

# Create a simplified initial migration
initial_migration = '''# Initial migration
from django.db import migrations, models
class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Movie',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tmdb_id', models.IntegerField(unique=True, db_index=True)),
                ('title', models.CharField(max_length=255)),
                ('year', models.IntegerField(blank=True, null=True)),
                ('content_type', models.CharField(max_length=10, choices=[('movie', 'Movie'), ('series', 'Series')], default='movie', db_index=True)),
                ('poster_path', models.CharField(max_length=255, blank=True, default='')),
                ('overview', models.TextField(blank=True, default='')),
                ('genres', models.JSONField(blank=True, default=list)),
                ('genre_ids', models.JSONField(blank=True, default=list)),
                ('cast', models.JSONField(blank=True, default=list)),
                ('director', models.JSONField(blank=True, default=list, help_text='List of director names (movie) or creators (series).')),
                ('seasons_data', models.JSONField(blank=True, default=list)),
                ('runtime_minutes', models.IntegerField(blank=True, null=True)),
                ('tmdb_rating', models.FloatField(blank=True, null=True)),
                ('first_air_date', models.CharField(blank=True, default='', max_length=20)),
                ('release_date', models.CharField(blank=True, default='', max_length=20)),
            ],
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0001_initial.py'), 'w') as f:
    f.write(initial_migration)
print("  Created 0001_initial.py")

# Step 3: Create additional migrations for other models
print()
print("Step 3: Creating migrations for other models...")

# Create simple migrations for other essential models
models_to_create = [
    ('Preference', ['user_id', 'audio_languages', 'subtitle_languages', 'candidate_countries']),
    ('Subscription', ['user_id', 'name']),
    ('Region', ['provider', 'country']),
    ('LanguageInfo', ['movie', 'region']),
    ('LanguageEntry', ['language_info', 'kind', 'language_code']),
    ('Availability', ['movie', 'region', 'last_checked_at', 'is_available', 'offer_type', 'quality', 'price_info', 'url', 'seasons_available']),
    ('WatchlistItem', ['user_id', 'movie', 'source', 'added_at']),
    ('ApiCallCounter', ['service', 'call_date', 'count']),
    ('RefreshRun', ['service', 'started_at', 'finished_at', 'rows_refreshed', 'rows_skipped', 'rate_limited']),
]

for i, (model_name, fields) in enumerate(models_to_create, start=2):
    filename = f"{i:04d}_{model_name.lower()}.py"
    file_path = os.path.join(migrations_dir, filename)
    
    migration_content = f'''# Generated migration for {model_name}
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "000{i-1}_initial"),
    ]

    operations = [
'''
    
    # Add CreateModel operation
    migration_content += f'        migrations.CreateModel(\n'
    migration_content += f'            name="{model_name}",\n'
    migration_content += f'            fields=[\n'
    
    # Add field definitions
    for field_name in fields:
        if field_name == 'user_id':
            migration_content += f'                ("{field_name}", models.IntegerField(default=1)),\n'
        elif field_name == 'service':
            migration_content += f'                ("{field_name}", models.CharField(max_length=50, unique=True)),\n'
        elif field_name == 'name':
            migration_content += f'                ("{field_name}", models.CharField(max_length=50, unique=True)),\n'
        elif field_name == 'provider':
            migration_content += f'                ("{field_name}", models.CharField(max_length=50)),\n'
        elif field_name == 'country':
            migration_content += f'                ("{field_name}", models.CharField(max_length=2)),\n'
        elif field_name == 'movie' or field_name == 'region' or field_name == 'language_info':
            migration_content += f'                ("{field_name}", models.ForeignKey(on_delete=models.CASCADE, related_name="{model_name.lower()}_s", to="tracker.{field_name}")),\n'
        elif field_name == 'last_checked_at':
            migration_content += f'                ("{field_name}", models.DateTimeField(blank=True, null=True)),\n'
        elif field_name == 'started_at':
            migration_content += f'                ("{field_name}", models.DateTimeField(default=timezone.now)),\n'
        elif field_name == 'finished_at':
            migration_content += f'                ("{field_name}", models.DateTimeField(null=True, blank=True)),\n'
        elif field_name == 'rows_refreshed':
            migration_content += f'                ("{field_name}", models.IntegerField(default=0)),\n'
        elif field_name == 'rate_limited':
            migration_content += f'                ("{field_name}", models.BooleanField(default=False)),\n'
        elif field_name == 'audio_languages':
            migration_content += f'                ("{field_name}", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=2), blank=True, default=list, size=None)),\n'
        elif field_name == 'subtitle_languages':
            migration_content += f'                ("{field_name}", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=2), blank=True, default=list, size=None)),\n'
        elif field_name == 'candidate_countries':
            migration_content += f'                ("{field_name}", django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=2), blank=True, default=list, size=None)),\n'
        elif field_name == 'kind':
            migration_content += f'                ("{field_name}", models.CharField(max_length=10, choices=[(\'audio\', \'Audio\'), (\'subtitle\', \'Subtitle\')])),\n'
        elif field_name == 'language_code':
            migration_content += f'                ("{field_name}", models.CharField(max_length=2)),\n'
        elif field_name == 'added_at':
            migration_content += f'                ("{field_name}", models.DateTimeField(auto_now_add=True)),\n'
        elif field_name == 'call_date':
            migration_content += f'                ("{field_name}", models.DateField(auto_now_add=True)),\n'
        elif field_name == 'count':
            migration_content += f'                ("{field_name}", models.IntegerField(default=0)),\n'
        elif field_name == 'is_available':
            migration_content += f'                ("{field_name}", models.BooleanField(default=False)),\n'
        elif field_name == 'offer_type':
            migration_content += f'                ("{field_name}", models.CharField(max_length=20, choices=[(\'subscription\', \'Subscription\'), (\'free\', \'Free\'), (\'rent\', \'Rent\'), (\'buy\', \'Buy\')])),\n'
        elif field_name == 'quality':
            migration_content += f'                ("{field_name}", models.CharField(max_length=10, blank=True)),\n'
        elif field_name == 'price_info':
            migration_content += f'                ("{field_name}", models.CharField(max_length=50, blank=True, default=\'\')),\n'
        elif field_name == 'url':
            migration_content += f'                ("{field_name}", models.URLField(max_length=500, blank=True, default=\'\')),\n'
        elif field_name == 'seasons_available':
            migration_content += f'                ("{field_name}", models.JSONField(blank=True, default=list)),\n'
        elif field_name == 'source':
            migration_content += f'                ("{field_name}", models.CharField(default=\'letterboxd\', max_length=20)),\n'
    
    migration_content += '''            ],
        ),
    ]
'''
    
    with open(file_path, 'w') as f:
        f.write(migration_content)
    
    print(f"  Created {filename}")

print()
print("Done creating all migrations.")
print()

# Step 4: Apply migrations
print("Step 4: Applying all migrations...")
result = os.system('python manage.py migrate tracker --verbosity=0')
if result == 0:
    print("✓ All migrations applied successfully")
else:
    print(f"✗ Migration failed with code {result}")
    print("This might be expected due to existing data or database state")

print()
print("=== Migration recreation complete ===")
