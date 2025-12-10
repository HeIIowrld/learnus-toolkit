#!/usr/bin/env python3
"""
Migration script to relocate pre-downloaded files from old directory structure
to new year/semester/course/week structure, and remove files without extensions.
"""
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple

# Import utilities
import sys
sys.path.insert(0, str(Path(__file__).parent))

from utils import sanitize_filename, parse_old_directory_name, has_extension, relocate_file_to_new_structure




def migrate_downloads(download_dir: Path, dry_run: bool = False):
    """
    Migrate files from old structure to new structure
    
    Args:
        download_dir: Path to downloads directory
        dry_run: If True, only print what would be done without actually moving files
    """
    if not download_dir.exists():
        print(f"Download directory does not exist: {download_dir}")
        return
    
    print(f"Starting migration {'(DRY RUN)' if dry_run else ''}...")
    print(f"Download directory: {download_dir}")
    print()
    
    stats = {
        'migrated': 0,
        'removed_no_ext': 0,
        'skipped': 0,
        'errors': 0
    }
    
    # Get all top-level directories
    top_level_dirs = [d for d in download_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    
    # Check if already using new structure (has year directories that are all numeric)
    year_dirs = [d for d in top_level_dirs if d.name.isdigit()]
    is_new_structure = len(year_dirs) > 0 and all(d.name.isdigit() for d in top_level_dirs[:3])
    
    if is_new_structure:
        print("⚠️  Detected new directory structure. Looking for old structure directories...")
        # Look for directories that don't match new structure pattern
        old_dirs = [d for d in top_level_dirs if not d.name.isdigit() and d.name != 'CONTENTS_HIERARCHY.md']
    else:
        print("📁 Detected old directory structure. Migrating all directories...")
        old_dirs = top_level_dirs
    
    for old_dir in old_dirs:
        if old_dir.name == 'CONTENTS_HIERARCHY.md':
            continue
            
        print(f"\n📂 Processing: {old_dir.name}")
        
        # Try to parse directory name
        parsed = parse_old_directory_name(old_dir.name)
        if not parsed:
            print(f"  ⚠️  Could not parse directory name, skipping: {old_dir.name}")
            stats['skipped'] += 1
            continue
        
        year, semester, course_name = parsed
        print(f"  📅 Year: {year}, Semester: {semester}, Course: {course_name}")
        
        # Sanitize components (for display only - utility function handles sanitization)
        year_clean = sanitize_filename(year)
        semester_clean = sanitize_filename(semester)
        course_clean = sanitize_filename(course_name)
        
        # Walk through all files in old directory
        for root, dirs, files in os.walk(old_dir):
            root_path = Path(root)
            rel_path = root_path.relative_to(old_dir)
            
            # Determine week/section name
            if str(rel_path) == '.':
                # Files in root of course directory - put in "General" week
                week_name = "General"
            else:
                # Use the relative path as week name
                week_name = str(rel_path)
            
            week_clean = sanitize_filename(week_name)
            
            # Create new directory structure
            new_week_dir = download_dir / year_clean / semester_clean / course_clean / week_clean
            
                        # Process files
                        for file in files:
                            file_path = root_path / file
                            
                            # Skip hidden files
                            if file.startswith('.'):
                                continue
                            
                            # Remove files without extensions
                            if not has_extension(file):
                                print(f"  🗑️  Removing file without extension: {file}")
                                stats['removed_no_ext'] += 1
                                if not dry_run:
                                    try:
                                        file_path.unlink()
                                    except Exception as e:
                                        print(f"    ❌ Error removing file: {e}")
                                        stats['errors'] += 1
                                continue
                            
                            # Skip JSON metadata files (they'll be regenerated if needed)
                            if file.endswith('.json') and 'metadata' in file.lower():
                                continue
                            
                            # Use utility function to relocate
                            if not dry_run:
                                new_file_path = relocate_file_to_new_structure(
                                    file_path, download_dir, year, semester, course_name, week_name
                                )
                                if new_file_path:
                                    print(f"  📦 Migrated: {file} -> {new_file_path.relative_to(download_dir)}")
                                    stats['migrated'] += 1
                                else:
                                    print(f"  ⚠️  Could not migrate: {file}")
                                    stats['errors'] += 1
                            else:
                                new_file_path = download_dir / year_clean / semester_clean / course_clean / week_clean / file
                                print(f"  📦 Would migrate: {file} -> {new_file_path.relative_to(download_dir)}")
                                stats['migrated'] += 1
    
    # Remove empty old directories
    if not dry_run:
        print("\n🧹 Cleaning up empty directories...")
        for old_dir in old_dirs:
            if old_dir.exists() and old_dir.name != 'CONTENTS_HIERARCHY.md':
                try:
                    # Check if directory is empty
                    if not any(old_dir.iterdir()):
                        print(f"  🗑️  Removing empty directory: {old_dir.name}")
                        old_dir.rmdir()
                    else:
                        # Try to remove recursively (may contain empty subdirectories)
                        try:
                            shutil.rmtree(old_dir)
                            print(f"  🗑️  Removed directory: {old_dir.name}")
                        except Exception:
                            pass  # Directory not empty, skip
                except Exception as e:
                    print(f"  ⚠️  Could not remove directory {old_dir.name}: {e}")
    
    # Print summary
    print("\n" + "="*60)
    print("Migration Summary:")
    print(f"  ✅ Migrated: {stats['migrated']} files")
    print(f"  🗑️  Removed (no extension): {stats['removed_no_ext']} files")
    print(f"  ⏭️  Skipped: {stats['skipped']} files")
    print(f"  ❌ Errors: {stats['errors']} files")
    print("="*60)
    
    if dry_run:
        print("\n⚠️  This was a DRY RUN. No files were actually moved.")
        print("   Run without --dry-run to perform the migration.")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate downloads from old to new directory structure')
    parser.add_argument('--download-dir', type=str, default='downloads',
                       help='Path to downloads directory (default: downloads)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Dry run mode - show what would be done without actually moving files')
    
    args = parser.parse_args()
    
    download_dir = Path(args.download_dir).resolve()
    
    migrate_downloads(download_dir, dry_run=args.dry_run)

