import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
django.setup()

import logging
logging.basicConfig(level=logging.DEBUG)

from tracker.scrapers.adapters import HydraHDScraper
scraper = HydraHDScraper()
print(scraper.run('batman'))
