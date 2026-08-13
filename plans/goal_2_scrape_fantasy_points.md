# Goal 2: Scrape Fantasy Football Half-PPR Points (Last 5 Years)

## Steps

1. **Identify Data Source**
   Use **Pro Football Reference** (`https://www.pro-football-reference.com`) as the data source. Its fantasy football scoring pages (e.g., `https://www.pro-football-reference.com/years/{YEAR}/fantasy.htm`) provide publicly accessible, season-by-season half-PPR point totals by player and position — no authentication required. These pages can be scraped with standard Python tools like `requests` and `BeautifulSoup`.

2. **Set Up Scraping Environment**
   - **Create the project directory structure:**
     ```
     fantasy_football_projects/
     ├── data/               # Raw and cleaned output files (CSV, SQLite, etc.)
     ├── scrapers/
     │   └── pfr_fantasy.py  # Main scraping script
     ├── requirements.txt
     └── README.md
     ```
   - **Create and activate a Python virtual environment:**
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```
   - **Install required libraries:**
     ```bash
     pip install requests beautifulsoup4 pandas lxml
     ```
     - `requests` — fetches HTML content from Pro Football Reference URLs
     - `beautifulsoup4` — parses and navigates the HTML to extract table data
     - `lxml` — fast HTML/XML parser used as the backend for BeautifulSoup
     - `pandas` — structures, cleans, and exports the scraped data
   - **Save dependencies** to `requirements.txt`:
     ```bash
     pip freeze > requirements.txt
     ```
   - **Verify access** to the target URL by making a test request and confirming a `200` status code:
     ```python
     import requests
     url = "https://www.pro-football-reference.com/years/2024/fantasy.htm"
     response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
     print(response.status_code)  # Should print 200
     ```
     > **Note:** Pro Football Reference may rate-limit or block requests without a `User-Agent` header. Always include one and add a short delay (e.g., `time.sleep(3)`) between requests to be respectful of the site's server.

   > ⚠️ **403 Warning (discovered during setup):** Pro Football Reference consistently returns `403 Forbidden` for automated requests, even with full browser-like headers. This is expected and common with PFR. To work around this in step 3, consider one of the following approaches:
   > - Use **`selenium`** or **`playwright`** to drive a real browser session
   > - Use **`pandas.read_html()`** which can sometimes bypass basic bot detection
   > - Inject **cookies from a real browser session** into the request headers

3. **Build the Scraper**
   Write a script to extract player names, positions, and half-PPR point totals for each of the last five seasons.

4. **Clean and Structure the Data**
   Normalize raw scraped data into a consistent format (player, position, season, total points) and handle missing or inconsistent values.

5. **Store the Data**
   Save the cleaned data to a structured format (e.g., CSV or SQLite) for use in downstream analysis.
