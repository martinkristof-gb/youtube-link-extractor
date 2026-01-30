from flask import Flask, render_template, request, jsonify
import requests
import re
import json
import concurrent.futures
from functools import lru_cache
import google.generativeai as genai
import os

app = Flask(__name__)

@lru_cache(maxsize=100)
def shorten_with_gemini(text, api_key):
    if not api_key:
        return None
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"Shorten the following product title to maximum 25 characters. It must make sense and be catchy. Original: '{text}'. Return ONLY the shortened title."
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return None

@lru_cache(maxsize=100)
def get_page_title(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
             'Accept-Language': 'en-US,en;q=0.9',
        }
        res = requests.get(url, headers=headers, timeout=5)
        
        # Try to use encoding from headers, default to utf-8
        if res.encoding is None or res.encoding.lower() == 'iso-8859-1':
            res.encoding = 'utf-8' # Default to utf-8 for modern web
        
        if res.status_code == 200:
            title_match = re.search(r'<title>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
                # Clean up common suffixes if needed, or keep as is.
                # User example: "Jelení jerky - GymBeam" -> looks like full title is wanted.
                return title
    except Exception as e:
        print(f"Error fetching title for {url}: {e}")
    return ""

def extract_links_with_text(description):
    if not description:
        return []
    lines = description.split('\n')
    extracted = []
    
    # Regex for finding URLs
    url_pattern = re.compile(r'https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)')
    
    for i, line in enumerate(lines):
        matches = list(url_pattern.finditer(line))
        
        for match in matches:
            url = match.group(0)
            
            # Strategy 1: Look for text on the same line
            text_on_line = line.replace(url, '').strip()
            # Clean up common separators and arrows
            text_on_line_cleaned = re.sub(r'^[:\-\|\s\u2190-\u2199]+|[:\-\|\s\u2190-\u2199]+$', '', text_on_line).strip()
            
            # Check if text is meaningful (has alphanumeric chars)
            has_alnum = bool(re.search(r'[a-zA-Z0-9]', text_on_line_cleaned))
            
            if text_on_line_cleaned and has_alnum:
                extracted.append({'text': text_on_line_cleaned, 'url': url})
            else:
                # Strategy 2: Look at the previous line
                found_prev = False
                if i > 0:
                    prev_line = lines[i-1].strip()
                    # Check if previous line is not a URL itself
                    if not url_pattern.search(prev_line) and prev_line:
                         extracted.append({'text': prev_line, 'url': url})
                         found_prev = True
                
                if not found_prev:
                     extracted.append({'text': 'Link', 'url': url})
                    
    return extracted

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/extract', methods=['POST'])
def extract():
    data = request.json
    video_url = data.get('url')
    api_key = data.get('api_key') or os.environ.get('GEMINI_API_KEY')
    
    if not video_url:
        return jsonify({'error': 'No URL provided'}), 400

    try:
        # Fetch the YouTube page
        # Note: We are scraping the HTML. This might be brittle if YouTube changes layout.
        # Ideally we'd use the API, but that requires an API key. 
        # Alternatively, we could use yt-dlp, but let's try requests first for simplicity.
        # We need headers to look like a browser.
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        response = requests.get(video_url, headers=headers)
        response.encoding = 'utf-8' # Ensure UTF-8 decoding
        response.raise_for_status()
        html = response.text
        
        # Extract title
        title_match = re.search(r'<title>(.*?)</title>', html)
        video_title = title_match.group(1).replace(' - YouTube', '') if title_match else 'Unknown Video'
        
        # Extract description. 
        # Meta tag: <meta name="description" content="...">
        description = ""
        desc_match = re.search(r'<meta name="description" content="(.*?)">', html)
        if desc_match:
            description = desc_match.group(1)
        
        # If meta description is truncated or missing, try to find the initial data JSON
        # This is optional but good for robustness.
        if not description or len(description) < 50:
             # Basic attempt to find fuller description in JSON
             # This is a bit complex regex, keeping it simple for now with meta tag
             # as it often contains the first few lines where links are.
             pass
             
        # Actually proper full description is often in "ytInitialData"
        # Let's try a robust fallback if meta is empty, but for now let's see.
        # The meta description often replaces newlines with spaces or just truncates.
        # A better way for full description without yt-dlp is looking for "shortDescription" in the HTML source
        # which is usually inside the player response.
        
        # Search for "shortDescription" in the text
        # pattern: "shortDescription":"..."
        # But it's json encoded.
        
        json_desc_match = re.search(r'"shortDescription":"(.*?)"', html)
        if json_desc_match:
            # Decode unicode escapes
            raw_desc = json_desc_match.group(1)
            # Python's string request might have escaped it.
            # Convert literal \n to actual newlines
            description = raw_desc.replace('\\n', '\n').encode().decode('unicode-escape') # simple unescape
        
        links = extract_links_with_text(description)
        
        # Enrich links with page titles concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_link = {executor.submit(get_page_title, link['url']): link for link in links}
            for future in concurrent.futures.as_completed(future_to_link):
                link = future_to_link[future]
                try:
                    page_title = future.result()
                    # Logic: Use title if found, else text.
                    full_title = page_title if page_title else link['text']
                    link['page_title'] = full_title
                    
                    # 1. First, clean the title of common site suffixes (e.g., " | SiteName")
                    cleaned_title = full_title.split(' | ')[0].strip() # Always prefer left part if pipe exists
                    
                    # 2. Determine Short Title
                    potential_short_title = cleaned_title
                    
                    if len(potential_short_title) <= 25:
                        # Perfect fit
                        short_title = potential_short_title
                    else:
                        # Too long. Try checking for " - Brand" pattern or similar
                        # Example: "Pink Burn Drink - GymBeam" -> "Pink Burn Drink"
                        if ' - ' in potential_short_title:
                            parts = potential_short_title.rsplit(' - ', 1)
                            # Only take the first part if it reduces length enough or is just better
                            first_part = parts[0].strip()
                            if len(first_part) <= 25:
                                short_title = first_part
                            else:
                                # First part still too long. Use Gemini or truncate.
                                short_title = potential_short_title 
                        else:
                             short_title = potential_short_title

                        if len(short_title) > 25:
                            gemini_title = shorten_with_gemini(short_title, api_key)
                            if gemini_title:
                                short_title = gemini_title
                            else:
                                short_title = short_title[:25].rstrip(' :,.|-')
                        
                    link['short_title'] = short_title
                    
                except Exception as exc:
                    print(f'{link["url"]} generated an exception: {exc}')
                    link['page_title'] = link['text']
                    link['short_title'] = link['text'][:25]

        return jsonify({
            'title': video_title,
            'description': description, # for debug if needed
            'links': links
        })
        
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
