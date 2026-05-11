#!/usr/bin/env python3
"""One-time fix: Re-categorize 'general' questions based on keyphrase matching."""
import sys
sys.path.insert(0, '/app/data/kb_programs')
from db import get_con
import re

def main():
    con = get_con()
    # Get categories from categories.json config (source of truth)
    import json
    from pathlib import Path
    KB_ROOT = Path(r"C:\knowledge-base")
    CONFIG = KB_ROOT / "config"
    try:
        with open(CONFIG / "categories.json") as f:
            cats_data = json.load(f)
            categories = [c for c in cats_data.get("categories", []) 
                         if c and c.lower() != 'general']
    except Exception as e:
        print(f"Error loading categories.json: {e}")
        categories = []
    
    if not categories:
        print("No categories found in config!")
        con.close()
        return
        
    print(f"Found {len(categories)} categories from config:")
    for c in categories:
        print(f"  - {c}")
    
    # Get all 'general' or NULL category questions
    general_q = con.execute("""
        SELECT id, keyphrase, question, category 
        FROM questions_research 
        WHERE category = 'general' OR category IS NULL
    """).fetchdf()
    
    print(f"\nFound {len(general_q)} questions to re-categorize")
    if len(general_q) == 0:
        print("No questions need fixing!")
        con.close()
        return
    
    # Show sample
    print("\nSample questions to fix:")
    for _, row in general_q.head(5).iterrows():
        print(f"  [{row['id']}] keyphrase='{row['keyphrase']}' -> question='{row['question'][:60]}...'")
    
    # Build category patterns from keyphrases
    cat_patterns = {}
    for cat in categories:
        # Get keyphrases for this category
        kp_rows = con.execute(
            "SELECT DISTINCT keyphrase FROM questions_research WHERE category = ? AND keyphrase IS NOT NULL",
            [cat]
        ).fetchdf()
        keyphrases = [k.lower() for k in kp_rows['keyphrase'].tolist() if k]
        if keyphrases:
            cat_patterns[cat] = keyphrases
    
    # Keyword-to-category semantic mappings (defined outside function for efficiency)
    keyword_map = {
        # Hanuman related
        'sunder kand': 'hanumana',
        'sundar kand': 'hanumana',
        'bajrang': 'hanumana',
        'bajrangbali': 'hanumana',
        'hanuman chalisa': 'hanumana',
        'pavan putra': 'hanumana',
        'anjani putra': 'hanumana',
        # Shiva related
        'trishul': 'shiva',
        'trishulam': 'shiva',
        'damru': 'shiva',
        'rudraksha': 'shiva',
        'rudraksh': 'shiva',
        'kailash': 'shiva',
        'mount kailash': 'shiva',
        'third eye': 'shiva',
        'neelkanth': 'shiva',
        'bholenath': 'shiva',
        'mahadev': 'shiva',
        'mahadeva': 'shiva',
        'nataraj': 'shiva',
        'nataraja': 'shiva',
        # Krishna related
        'radha': 'krishna',
        'radhe': 'krishna',
        'vrindavan': 'krishna',
        'mathura': 'krishna',
        'dwarka': 'krishna',
        'gita': 'krishna',
        'bhagavad gita': 'krishna',
        'flute': 'krishna',
        'bansuri': 'krishna',
        'sudarshan': 'krishna',
        'sudarshana': 'krishna',
        'chakra': 'krishna',
    }
    
    # Function to find best matching category
    def find_category(keyphrase, question, debug=False):
        if not keyphrase:
            return None
        kp_lower = keyphrase.lower()
        if debug:
            print(f"  DEBUG: Checking '{kp_lower}'")
        
        # First: keyword-to-category semantic mappings (HIGHEST PRIORITY)
        for keyword, cat in keyword_map.items():
            if keyword in kp_lower:
                if debug:
                    print(f"  DEBUG: Matched keyword '{keyword}' -> {cat}")
                return cat
        
        # Second: direct category name matching (with fuzzy logic)
        for cat in categories:
            cat_lower = cat.lower()
            # Exact containment
            if cat_lower in kp_lower:
                if debug:
                    print(f"  DEBUG: Direct match '{cat_lower}' in '{kp_lower}'")
                return cat
            # Fuzzy: check if keyphrase word starts with category (hanuman vs hanumana)
            words = kp_lower.replace('-', ' ').replace('_', ' ').split()
            for word in words:
                if len(word) >= 4:
                    if word.startswith(cat_lower) or cat_lower.startswith(word):
                        if debug:
                            print(f"  DEBUG: Fuzzy match '{word}' ~ '{cat_lower}'")
                        return cat
        
        if debug:
            print(f"  DEBUG: No match found")
        # Third: try pattern matching from existing keyphrases (for future use)
        best_cat = None
        best_score = 0
        for cat, patterns in cat_patterns.items():
            score = 0
            for pattern in patterns:
                if pattern == kp_lower:
                    score += 100
                elif pattern in kp_lower:
                    score += 50
                elif kp_lower in pattern:
                    score += 25
            if score > best_score:
                best_score = score
                best_cat = cat
        return best_cat
    
    # Preview assignments
    print("\n" + "="*60)
    print("PROPOSED RE-CATEGORIZATIONS:")
    print("="*60)
    
    assignments = []
    for _, row in general_q.iterrows():
        new_cat = find_category(row['keyphrase'], row['question'])
        if new_cat:
            assignments.append((row['id'], new_cat, row['keyphrase'], row['question'][:50]))
            print(f"  ID {row['id']}: '{row['keyphrase']}' -> {new_cat}")
    
    no_match = len(general_q) - len(assignments)
    if no_match > 0:
        print(f"\n  ({no_match} questions couldn't be matched - will stay as 'general')")
    
    # Confirm with user
    print("\n" + "="*60)
    if not assignments:
        print("No questions could be auto-categorized.")
        con.close()
        return
    
    confirm = input(f"\nUpdate {len(assignments)} questions? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Aborted.")
        con.close()
        return
    
    # Perform updates
    updated = 0
    for qid, new_cat, _, _ in assignments:
        con.execute("UPDATE questions_research SET category = ? WHERE id = ?", [new_cat, qid])
        updated += 1
    
    con.commit() if hasattr(con, 'commit') else None
    con.close()
    print(f"\n✅ Updated {updated} questions successfully!")

if __name__ == "__main__":
    main()