from django.db import migrations

from tracker.languages import CANONICAL_CODES, LANGUAGE_ALIAS_MAP, normalize_language_code


def normalize(val):
    if not val or not isinstance(val, str):
        return None
    v = val.strip()
    if not v:
        return None
    norm = normalize_language_code(v)
    if norm:
        return norm
    if len(v) == 2:
        return v.upper()
    return v


def migrate_languages(apps, schema_editor):
    Preference = apps.get_model("tracker", "Preference")
    LanguageEntry = apps.get_model("tracker", "LanguageEntry")

    # 1. Update Preferences
    for pref in Preference.objects.all():
        new_audio = []
        audio_seen = set()
        for item in pref.audio_languages or []:
            norm = normalize(item)
            if norm and norm not in audio_seen:
                audio_seen.add(norm)
                new_audio.append(norm)

        new_subs = []
        subs_seen = set()
        for item in pref.subtitle_languages or []:
            norm = normalize(item)
            if norm and norm not in subs_seen:
                subs_seen.add(norm)
                new_subs.append(norm)

        pref.audio_languages = new_audio
        pref.subtitle_languages = new_subs
        pref.save(update_fields=["audio_languages", "subtitle_languages"])

    # 2. Update LanguageEntry rows
    entries = LanguageEntry.objects.all()
    seen_entry_keys = set()
    entries_to_delete = []

    for entry in entries:
        norm = normalize(entry.language_code) or entry.language_code.upper()
        key = (entry.language_info_id, entry.kind, norm)
        if key in seen_entry_keys:
            entries_to_delete.append(entry.id)
        else:
            seen_entry_keys.add(key)
            if entry.language_code != norm:
                entry.language_code = norm
                entry.save(update_fields=["language_code"])

    if entries_to_delete:
        LanguageEntry.objects.filter(id__in=entries_to_delete).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0005_refreshrun"),
    ]

    operations = [
        migrations.RunPython(migrate_languages, migrations.RunPython.noop),
    ]
