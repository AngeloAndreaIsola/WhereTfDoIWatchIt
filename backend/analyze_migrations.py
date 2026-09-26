import os
import re

print("=== Analyzing Current Migration Issues ===")
print()

migrations_dir = 'tracker/migrations'

# Get all migration files
migration_files = [f for f in os.listdir(migrations_dir) if f.endswith('.py') and not f.startswith('__')]

print(f"Total migration files: {len(migration_files)}")
print()

# Parse migration numbers and dependencies
migrations = {}
for filename in migration_files:
    filepath = os.path.join(migrations_dir, filename)
    
    # Extract migration number
    match = re.match(r'(\d+)_', filename)
    if match:
        migration_num = match.group(1)
        
        # Read file
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Extract dependencies
        dep_match = re.search(r'dependencies\s*=\s*\[([^\]]+)\]', content)
        if dep_match:
            deps_str = dep_match.group(1)
            # Find all tracker dependencies
            tracker_deps = re.findall(r'\("tracker",\s*"([^"]+)"\)', deps_str)
        else:
            tracker_deps = []
        
        # Store info
        migrations[migration_num] = {
            'filename': filename,
            'path': filepath,
            'dependencies': tracker_deps
        }

# Check for issues
print("Checking for migration issues...")
print()

issues_found = []

# 1. Check for duplicate numbers
seen_nums = set()
for num in migrations.keys():
    if num in seen_nums:
        issues_found.append(f"  - Duplicate migration number: {num}")
    seen_nums.add(num)

# 2. Check for broken dependencies
for num, info in migrations.items():
    for dep in info['dependencies']:
        if dep not in migrations and dep != 'None':
            issues_found.append(f"  - Migration {num} references nonexistent dependency: {dep}")

# 3. Check for circular dependencies
for num, info in migrations.items():
    for dep in info['dependencies']:
        if dep in migrations:
            # Simple check - if A depends on B and B depends on A (direct)
            if dep in migrations and num in migrations[dep]['dependencies']:
                issues_found.append(f"  - Circular dependency between {num} and {dep}")

# 4. Check for gaps in numbering
nums = sorted(migrations.keys())
for i in range(len(nums) - 1):
    current_num = int(nums[i])
    next_num = int(nums[i + 1])
    if next_num - current_num > 1:
        issues_found.append(f"  - Gap in migration numbering: from {current_num} to {next_num}")

# Report issues
if issues_found:
    print(f"Found {len(issues_found)} issues:")
    for issue in issues_found:
        print(issue)
else:
    print("✓ No issues found")

print()
print("=== Migration List (sorted) ===")
for num in sorted(migrations.keys()):
    info = migrations[num]
    deps = info['dependencies']
    dep_str = ', '.join(deps) if deps else 'none'
    print(f"  {num} - {info['filename']} (depends on: {dep_str})")
