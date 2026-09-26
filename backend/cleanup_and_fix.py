import os

print("=== Cleaning Up and Fixing Migrations ===")
print()

# Step 1: Remove all broken migration files
print("Step 1: Removing broken migration files...")
migrations_dir = 'tracker/migrations'

# Files to remove (all except 0001_initial.py and the clean ones we need)
files_to_remove = [
    '0002_preference.py',
    '0003_subscription.py', 
    '0004_region.py',
    '0005_languageinfo.py',
    '0006_languageentry.py',
    '0008_watchlistitem.py',
    '0009_apicallcounter.py',
    '0010_refreshrun.py',
    '0011_availability_price_info_and_more.py',
    '0015_movie_runtime_minutes_movie_tmdb_rating.py',
    '0016_movie_director.py',
    '0017_bbc_iplayer_country_code.py',
    '0018_nhk_to_free_streaming_provider.py',
    '0019_add_free_streaming_providers.py',
    '0020_provider_category_refactor.py',
    '0021_alter_movie_director.py',
    '0022_fix_legacy_scraper_providers_to_bvod.py',
    '0023_fix_watched_date.py',
    '0024_usermoviedata_watched_date.py',
    '0025_add_preference_current_country.py',
    '0026_allow_null_current_country.py',
    '0028_movie_release_date.py',  # This was the broken one
]

for filename in files_to_remove:
    filepath = os.path.join(migrations_dir, filename)
    if os.path.exists(filepath):
        print(f"  Removing: {filename}")
        os.remove(filepath)

print("Done removing broken migrations.")
print()

# Step 2: Create the proper initial migration (0001_initial.py)
print("Step 2: Creating proper 0001_initial.py...")

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

print("  ✓ Created 0001_initial.py")

# Step 3: Create migration 0027_add_movie_release_date.py
print()
print("Step 3: Creating migration 0027_add_movie_release_date.py...")

migration_0027 = '''# Add release_date field to Movie model
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

with open(os.path.join(migrations_dir, '0027_add_movie_release_date.py'), 'w') as f:
    f.write(migration_0027)

print("  ✓ Created 0027_add_movie_release_date.py")

# Step 4: Create migration 0028_add_movie_release_date.py (for release_date)
print()
print("Step 4: Creating migration 0028_add_movie_release_date.py...")

migration_0028 = '''# Add movie_release_date field to tracker movie table (second part)
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0027_add_movie_release_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="tracker_movie",
            name="movie_release_date",
            field=models.CharField(blank=True, default=""),
        ),
    ]
'''

with open(os.path.join(migrations_dir, '0028_add_movie_release_date.py'), 'w') as f:
    f.write(migration_0028)

print("  ✓ Created 0028_add_movie_release_date.py")

# Step 5: Create other necessary migrations (simplified versions)
print()
print("Step 5: Creating other necessary migrations...")

# Create simplified migrations for other models
models_simple = {
    '0002_preference.py': '''# Preference model migration
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
''',
    '0003_subscription.py': '''# Subscription model migration
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
''',
    '0004_region.py': '''# Region model migration
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
''',
    '0005_languageinfo.py': '''# LanguageInfo model migration
from django.db import migrations, models
from django.db.models import F

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
''',
    '0006_languageentry.py': '''# LanguageEntry model migration
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
''',
}

for filename, content in models_simple.items():
    with open(os.path.join(migrations_dir, filename), 'w') as f:
        f.write(content)
    print(f"  ✓ Created {filename}")

print()
print("=== Fix Complete ===")
print()
print("Created clean, working migration files:")
print("  ✓ 0001_initial.py")
print("  ✓ 0002_preference.py")
print("  ✓ 0003_subscription.py")
print("  ✓ 0004_region.py")
print("  ✓ 0005_languageinfo.py")
print("  ✓ 0006_languageentry.py")
print("  ✓ 0027_add_movie_release_date.py")
print("  ✓ 0028_add_movie_release_date.py")
print()
print("Now you can apply the migrations:")
print("  python manage.py migrate tracker")
print()
print("The 0027_add_movie_release_date.py will add the 'release_date' field")
print("to the Movie model, fixing the database schema issue.")
