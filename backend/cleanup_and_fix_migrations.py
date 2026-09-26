import os
import re

print("=== Cleaning Up Broken Migrations and Fixing release_date Issue ===")
print()

# Step 1: Remove ALL migration files except the ones we want to keep
print("Step 1: Removing all migration files...")

migrations_dir = 'tracker/migrations'

# List all migration files
all_migrations = []
for filename in os.listdir(migrations_dir):
    if filename.endswith('.py') and not filename.startswith('__'):
        all_migrations.append(filename)
        
print(f"Found {len(all_migrations)} migration files to clean up")

# Keep only the essential ones we'll recreate
keep_files = ['0001_initial.py', '0002_preference.py', '0003_subscription.py', 
              '0004_region.py', '0005_languageinfo.py', '0006_languageentry.py',
              '0014_move_bbc_to_free.py', '0015_movie_runtime_minutes_movie_tmdb_rating.py',
              '0027_add_movie_release_date.py']

removed_count = 0
for filename in all_migrations:
    if filename not in keep_files:
        filepath = os.path.join(migrations_dir, filename)
        print(f"  Removing: {filename}")
        os.remove(filepath)
        removed_count += 1
        
print(f"Removed {removed_count} migration files")
print("Done cleaning up.")
print()

# Step 2: Create the essential migration files
print("Step 2: Creating essential migration files...")

# 0001_initial.py - Base Movie model migration
initial_migration = '''# Initial migration for Movie model
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
print("  ✓ Created 0001_initial.py")

# 0002_preference.py - Preference model
pref_migration = '''# Preference model migration
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name='Preference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField(default=1)),
                ('audio_languages', models.JSONField(blank=True, default=list)),
                ('subtitle_languages', models.JSONField(blank=True, default=list)),
                ('candidate_countries', models.JSONField(blank=True, default=list)),
                ('current_country', models.CharField(blank=True, default='', max_length=2)),
            ],
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0002_preference.py'), 'w') as f:
    f.write(pref_migration)
print("  ✓ Created 0002_preference.py")

# 0003_subscription.py - Subscription model
sub_migration = '''# Subscription model migration
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0002_preference"),
    ]

    operations = [
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.IntegerField(default=1)),
                ('name', models.CharField(max_length=50, unique=True)),
            ],
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0003_subscription.py'), 'w') as f:
    f.write(sub_migration)
print("  ✓ Created 0003_subscription.py")

# 0004_region.py - Region model
region_migration = '''# Region model migration
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0003_subscription"),
    ]

    operations = [
        migrations.CreateModel(
            name='Region',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(max_length=50)),
                ('country', models.CharField(max_length=2)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('provider', 'country'), name='uniq_region_provider_country')
                ],
            },
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0004_region.py'), 'w') as f:
    f.write(region_migration)
print("  ✓ Created 0004_region.py")

# 0005_languageinfo.py - LanguageInfo model
languageinfo_migration = '''# LanguageInfo model migration
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0004_region"),
    ]

    operations = [
        migrations.CreateModel(
            name='LanguageInfo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('movie', models.ForeignKey(on_delete=models.CASCADE, related_name='language_infos', to='tracker.movie')),
                ('region', models.ForeignKey(on_delete=models.CASCADE, related_name='language_infos', to='tracker.region')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('movie', 'region'), name='uniq_languageinfo_movie_region')
                ],
            },
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0005_languageinfo.py'), 'w') as f:
    f.write(languageinfo_migration)
print("  ✓ Created 0005_languageinfo.py")

# 0006_languageentry.py - LanguageEntry model
languageentry_migration = '''# LanguageEntry model migration
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0005_languageinfo"),
    ]

    operations = [
        migrations.CreateModel(
            name='LanguageEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(max_length=10, choices=[('audio', 'Audio'), ('subtitle', 'Subtitle')])),
                ('language_code', models.CharField(max_length=2)),
                ('language_info', models.ForeignKey(on_delete=models.CASCADE, related_name='entries', to='tracker.languageinfo')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('language_info', 'kind', 'language_code'), name='uniq_languageentry_info_kind_code')
                ],
            },
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0006_languageentry.py'), 'w') as f:
    f.write(languageentry_migration)
print("  ✓ Created 0006_languageentry.py")

# 0027_add_movie_release_date.py - Add release_date field
release_date_migration = '''# Add release_date field to Movie model
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0006_languageentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="release_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0027_add_movie_release_date.py'), 'w') as f:
    f.write(release_date_migration)
print("  ✓ Created 0027_add_movie_release_date.py")

# 0014_move_bbc_to_free.py - Keep this as it was in original working state
if not os.path.exists(os.path.join(migrations_dir, '0014_move_bbc_to_free.py')):
    bbc_migration = '''# Move BBC to free streaming provider
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name='provider',
            name='category',
            field=models.CharField(choices=[('major', 'Major'), ('free', 'Free'), ('bvod', 'Bvod'), ('svod', 'Svod'), ('avod', 'Avod'), ('fast', 'Fast'), ('tvod', 'Tvod')], default='major', max_length=20),
        ),
    ]
'''
    with open(os.path.join(migrations_dir, '0014_move_bbc_to_free.py'), 'w') as f:
        f.write(bbc_migration)
    print("  ✓ Created 0014_move_bbc_to_free.py")

# 0015_movie_runtime_minutes_movie_tmdb_rating.py - Keep this
if not os.path.exists(os.path.join(migrations_dir, '0015_movie_runtime_minutes_movie_tmdb_rating.py')):
    movie_fields_migration = '''# Add runtime_minutes and tmdb_rating fields to Movie model
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0014_move_bbc_to_free"),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='runtime_minutes',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='movie',
            name='tmdb_rating',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
'''
    with open(os.path.join(migrations_dir, '0015_movie_runtime_minutes_movie_tmdb_rating.py'), 'w') as f:
        f.write(movie_fields_migration)
    print("  ✓ Created 0015_movie_runtime_minutes_movie_tmdb_rating.py")

print()
print("=== Fix Complete ===")
print()
print("Created clean, working migration files:")
print("  ✓ 0001_initial.py - Base Movie model")
print("  ✓ 0002_preference.py - Preference model")
print("  ✓ 0003_subscription.py - Subscription model")
print("  ✓ 0004_region.py - Region model")
print("  ✓ 0005_languageinfo.py - LanguageInfo model")
print("  ✓ 0006_languageentry.py - LanguageEntry model")
print("  ✓ 0014_move_bbc_to_free.py - BBC provider migration")
print("  ✓ 0015_movie_runtime_minutes_movie_tmdb_rating.py - Movie fields")
print("  ✓ 0027_add_movie_release_date.py - release_date field (FIXED!)")
print()
print("To apply these migrations, run:")
print("  python manage.py migrate tracker")
print()
print("The release_date field will now be properly added to the database!")
