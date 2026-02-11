# OpenAI GPTs Integration

## Create a Custom GPT

### 1. Deploy the API Server

You need a publicly accessible URL:

**Option A: ngrok (for development/testing)**
```bash
# Start the server
python -m src.api_server --port 8765

# In another terminal, expose with ngrok
ngrok http 8765
# You'll get a URL like https://abc123.ngrok.io
```

**Option B: Deploy to cloud**
```bash
# Docker
docker build -t flyto-indexer .
docker run -p 8765:8765 flyto-indexer

# Or deploy to your VPS
```

### 2. Create the GPT

1. Go to https://chat.openai.com/gpts/editor
2. Click "Create a GPT"
3. Configure as follows:

**Name:** Code Intelligence Assistant

**Description:**
Search code, understand file purposes, and analyze the impact of code changes using flyto-indexer.

**Instructions:**
```
You are a code intelligence assistant. You can:

1. Search code: When the user says "find authentication code", call searchCode
2. Get file info: When the user asks "what does auth.py do?", call getFileInfo
3. Impact analysis: When the user says "I want to modify the login function", call impactAnalysis

When responding:
- List found files and their purposes
- Explain each file's category and keywords
- If the user wants to modify code, analyze the impact first
```

### 3. Configure Actions

1. Click "Configure" → "Create new action"
2. Enter the OpenAPI Schema:

```yaml
openapi: 3.1.0
info:
  title: Flyto Indexer API
  description: Semantic code indexing API
  version: 1.0.0
servers:
  - url: https://your-server-url.com  # Replace with your URL
paths:
  /search:
    post:
      operationId: searchCode
      summary: Search code
      description: Search for relevant code files by keyword
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                query:
                  type: string
                  description: Search keyword
                max_results:
                  type: integer
                  default: 10
              required:
                - query
      responses:
        '200':
          description: Search results

  /file/info:
    post:
      operationId: getFileInfo
      summary: Get file info
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                path:
                  type: string
              required:
                - path
      responses:
        '200':
          description: File information

  /impact:
    post:
      operationId: impactAnalysis
      summary: Impact analysis
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                symbol_id:
                  type: string
              required:
                - symbol_id
      responses:
        '200':
          description: Impact analysis result

  /categories:
    get:
      operationId: listCategories
      summary: List categories
      responses:
        '200':
          description: Category list

  /stats:
    get:
      operationId: getStats
      summary: Index statistics
      responses:
        '200':
          description: Statistics
```

3. Click "Save"

### 4. Usage Example

In your GPT, type:
```
Find the code that handles user authentication
```

The GPT will:
1. Call the searchCode API
2. Return relevant files and their purposes
3. Provide suggestions

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search` | POST | Keyword search |
| `/file/info` | POST | Get file metadata |
| `/file/symbols` | POST | List file symbols |
| `/impact` | POST | Impact analysis |
| `/categories` | GET | List categories |
| `/apis` | GET | List API endpoints |
| `/stats` | GET | Index statistics |
| `/openapi.json` | GET | OpenAPI spec |
| `/health` | GET | Health check |

---

## Auto-fetch OpenAPI Schema

Access directly:
```
https://your-server-url.com/openapi.json
```

You can paste this URL directly into the GPT Actions configuration.
