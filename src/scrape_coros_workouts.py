from playwright.sync_api import sync_playwright
import duckdb
import time
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'strava_warehouse.duckdb')

def scrape_workouts_dom():
    all_workouts = []
    
    with sync_playwright() as p:
        print("Launching browser...")
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 1. Navigate
        print("Navigating to Coros Training...")
        page.goto("https://coros.com/training", wait_until="networkidle")
        
        # 2. Select "Workouts" tab
        # Based on screenshots/investigation, it might be a tab or checkbox.
        # Let's try to click the text 'Workouts' if it appears as a filter/tab
        try:
            # Often tabs are: "Training Plans" | "Workouts"
            # We look for the Workouts text to click.
            print("Selecting 'Workouts'...")
            page.get_by_text("Workouts", exact=True).first.click()
            time.sleep(2) # Wait for UI update
            
            # 3. Select "Run" filter
            print("Selecting 'Run' filter...")
            page.get_by_text("Run", exact=True).first.click()
            time.sleep(2)
        except Exception as e:
            print(f"Warning during filtering: {e}. Trying to proceed...")

        # 4. Pagination Loop
        page_num = 1
        last_item_count = 0
        
        while True:
            print(f"Scraping page {page_num}...")
            
            # Wait for items to be present
            # Based on subagent, buttons have class 'getit-btn'
            try:
                page.wait_for_selector(".getit-btn", timeout=5000)
            except:
                print("No items found on this page.")
                break
            
            # Scrape contents
            # We find all containers that have a 'getit-btn'
            items = page.query_selector_all(".item-content-info, .training-item") 
            # Note: class names might vary, so we can also just find all 'getit-btn' and go up to parent.
            
            if not items:
                # Fallback strategy
                buttons = page.query_selector_all(".getit-btn")
                items = [btn.evaluate("el => el.parentElement.parentElement") for btn in buttons]
            
            print(f"Found {len(items)} items on page.")
            
            for item in items:
                try:
                    # Extract text content carefully
                    text = item.inner_text()
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    # Heuristic parsing
                    # Usually: [Tags] -> Title -> Description -> Button
                    # Or: Title -> Description -> ...
                    
                    name = ""
                    description = ""
                    tags = []
                    url = ""
                    
                    # Try to find specific elements if possible
                    title_el = item.query_selector("h1, h2, h3, .title, .item-title")
                    if title_el:
                        name = title_el.inner_text().strip()
                    
                    desc_el = item.query_selector("p, .desc, .description")
                    if desc_el:
                        description = desc_el.inner_text().strip()
                        
                    # Link
                    link_el = item.query_selector("a.getit-btn")
                    if link_el:
                        url = link_el.get_attribute("href")
                        
                    # Tags: usually small spans
                    tag_els = item.query_selector_all(".tag, .arco-tag, span")
                    for t in tag_els:
                        tag_text = t.inner_text().strip()
                        if tag_text and tag_text != name and tag_text != "VIEW DETAILS" and len(tag_text) < 30:
                            tags.append(tag_text)
                    
                    # Determine category (TSB logic) based on text analysis
                    category = 'Aerobic'
                    full_text = (str(name) + " " + str(description) + " " + " ".join(tags)).lower()
                    
                    if any(x in full_text for x in ['recovery', 'easy', 'warm up']):
                        category = 'Recovery'
                    elif any(x in full_text for x in ['threshold', 'tempo', 'steady']):
                        category = 'Threshold'
                    elif any(x in full_text for x in ['interval', 'vo2', 'hill', 'fartlek', 'track', 'sprint', 'anaerobic']):
                        if 'sprint' in full_text or 'anaerobic' in full_text:
                             category = 'Anaerobic'
                        else:
                             category = 'VO2Max'
                    elif any(x in full_text for x in ['base', 'long run', 'aerobic']):
                        category = 'Aerobic'

                    if name and url:
                        all_workouts.append({
                            'id': url, # Use URL as ID if programId not obvious
                            'name': name,
                            'description': description,
                            'category': category,
                            'tags': ", ".join(list(set(tags))),
                            'url': url
                        })
                except Exception as e:
                    continue

            # Go to next page
            next_btn = page.query_selector(".arco-pagination-item-next")
            
            # Check if disabled or missing
            if not next_btn:
                print("No next button found.")
                break
                
            classes = next_btn.get_attribute("class")
            if "disabled" in classes:
                print("Reached last page.")
                break
                
            next_btn.click()
            page_num += 1
            time.sleep(2) # Wait for load
            
            # Safety break
            if page_num > 50: break
            
        browser.close()
        
    print(f"Total workouts scraped: {len(all_workouts)}")
    return all_workouts

def ingest_to_duckdb(workouts, min_expected=1):
    """Upserts scraped workouts into dim_workouts.

    Previously this dropped and recreated the whole table on every run, so
    if Coros changed their page layout and the scrape only partially
    succeeded (a very plausible failure mode given the best-effort,
    try/except-per-item selector logic in scrape_workouts_dom), a complete
    table was silently replaced with a small partial one. Now it upserts by
    workout_id (URL) and refuses to proceed at all if the new scrape returned
    suspiciously few workouts compared to what's already stored.
    """
    con = duckdb.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_workouts (
            workout_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            description VARCHAR,
            category VARCHAR,
            tags VARCHAR,
            url VARCHAR,
            source VARCHAR DEFAULT 'COROS'
        );
    """)

    existing_count = con.execute("SELECT COUNT(*) FROM dim_workouts").fetchone()[0]
    # Refuse to (potentially) shrink an existing library via a bad scrape.
    # min_expected guards the from-empty case (a fresh DB legitimately starts
    # at 0); half of the existing count is a permissive guard against a
    # genuinely partial scrape, not against normal day-to-day fluctuation.
    if existing_count > 0 and len(workouts) < max(min_expected, existing_count // 2):
        print(
            f"Refusing to ingest: scrape returned {len(workouts)} workouts, "
            f"but dim_workouts already has {existing_count}. This looks like "
            f"a partial/failed scrape (e.g. Coros changed their page layout) "
            f"rather than a real shrink of the workout library — not touching "
            f"existing data. Re-run once the scraper is confirmed working."
        )
        con.close()
        return

    print("Upserting into DuckDB...")
    count = 0
    seen_urls = set()
    buffer = []
    for w in workouts:
        if w['url'] in seen_urls: continue
        seen_urls.add(w['url'])
        buffer.append((w['url'], w['name'], w['description'], w['category'], w['tags'], w['url']))
        count += 1

    if buffer:
        con.executemany("""
            INSERT INTO dim_workouts (workout_id, name, description, category, tags, url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (workout_id) DO UPDATE SET
                name = EXCLUDED.name, description = EXCLUDED.description,
                category = EXCLUDED.category, tags = EXCLUDED.tags, url = EXCLUDED.url
        """, buffer)

    con.close()
    print(f"Successfully upserted {count} unique workouts.")

if __name__ == "__main__":
    workouts = scrape_workouts_dom()
    if workouts:
        ingest_to_duckdb(workouts)
