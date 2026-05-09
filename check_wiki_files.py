# Check what wiki files exist in knowledge base
from pathlib import Path

KB_ROOT = Path(r"C:\knowledge-base")
wiki_root = KB_ROOT / "wiki"

print("Wiki root exists:", wiki_root.exists())
print("\nAll .md files found:")
all_md = list(wiki_root.rglob("*.md"))
for f in all_md[:20]:
    print(" ", f)
print(f"\nTotal .md files: {len(all_md)}")

print("\nverified_facts.md files:")
vf = list(wiki_root.rglob("verified_facts.md"))
for f in vf:
    print(" ", f)
print(f"Total: {len(vf)}")

input("\nPress Enter to close...")
