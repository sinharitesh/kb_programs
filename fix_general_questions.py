#!/usr/bin/env python3
"""One-time fix: Re-categorize 'general' questions based on keyphrase matching."""
import sys
sys.path.insert(0, '/app/data/kb_programs')
from db import get_con
import re

def main():
    con = get_con()
    
    # Get all valid categories (excluding 'general')
    all_cats = con.execute("""
        SELECT DISTINCT category FROM keyword_intelligence 
        WHERE category IS NOT NULL AND category != 'general'
        UNION
        SELECT DISTINCT category FROM questions_research 
        WHERE category IS NOT NULL AND category != 'general'
    """).fetchdf()
    categories = sorted([c for c in all_cats['category'].tolist() if c])
    print(f"Found {len(categories)} valid categories:")
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
    
    # Function to find best matching category
    def find_category(keyphrase, question):
        if not keyphrase:
            return None
        kp_lower = keyphrase.lower()
        best_cat = None
        best_score = 0
        
        for cat, patterns in cat_patterns.items():
            score = 0
            for pattern in patterns:
                # Exact match gets highest score
                if pattern == kp_lower:
                    score += 100
                # Contains pattern
                elif pattern in kp_lower:
                    score += 50
                # Pattern contains keyphrase
                elif kp_lower in pattern:
                    score += 25
                # Word overlap
                elif any(word in kp_lower for word in pattern.split() if len(word) > 3):
                    score += 10
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
