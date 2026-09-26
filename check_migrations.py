import os
import re

migrations_dir = 'backend/tracker/migrations'
print("Migration files:")
for filename in sorted(os.listdir(migrations_dir)):
    if filename.endswith('.py') and not filename.startswith('__'):
        print(f"  {filename}")
