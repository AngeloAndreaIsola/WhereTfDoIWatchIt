import os
import re

migrations_dir = 'backend/tracker/migrations'
print("Migration files and dependencies:")
print("=" * 60)

# Read all migration files
for filename in sorted(os.listdir(migrations_dir)):
    if filename.endswith('.py') and not filename.startswith('__'):
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath, 'r') as f:
            content = f.read()
            
        # Extract migration name from filename
        match = re.search(r'(\d+)_', filename)
        if match:
            migration_num = match.group(1)
            print(f"\n{migration_num} - {filename}:")
            
            # Check dependencies
            dep_match = re.search(r'dependencies\s*=\s*\[([^\]]+)\]', content)
            if dep_match:
                deps = dep_match.group(1)
                # Extract the tracker migration name
                tracker_dep = re.search(r'\("tracker",\s*"([^"]+)"\)', deps)
                if tracker_dep:
                    print(f"  Depends on: {tracker_dep.group(1)}")
                else:
                    print(f"  Dependencies: {deps.strip()}")
