# Context Interrupter — Technical Architecture

## System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                           GITHUB                                     │
│  (Source: Issues, PRs, Mentions)                                     │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
                    (Webhooks / API Polling)
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │   CONTEXT INTERRUPTER DAEMON          │
        │   (Main orchestrator process)         │
        │                                       │
        │  ┌─────────────────────────────────┐ │
        │  │  GitHub Listener                │ │
        │  │  (Webhook server on :9001)      │ │
        │  │  • Receive PR updates           │ │
        │  │  • Receive issue notifications  │ │
        │  │  • Parse and validate           │ │
        │  └────────┬────────────────────────┘ │
        │           │                          │
        │           ▼                          │
        │  ┌─────────────────────────────────┐ │
        │  │  Notification Queue             │ │
        │  │  (In-memory + JSON persistence) │ │
        │  │  • Store raw notifications      │ │
        │  │  • Track read/unread            │ │
        │  │  • Handle deduplication         │ │
        │  └────────┬────────────────────────┘ │
        │           │                          │
        │           ▼                          │
        │  ┌─────────────────────────────────┐ │
        │  │  Summarizer (LLM Pipeline)      │ │
        │  │  • Extract key info             │ │
        │  │  • Call local Ollama            │ │
        │  │  • Format summary               │ │
        │  │  • Cache results                │ │
        │  └────────┬────────────────────────┘ │
        │           │                          │
        │           ▼                          │
        │  ┌─────────────────────────────────┐ │
        │  │  Release Logic                  │ │
        │  │  Monitors:                      │ │
        │  │  • Git commits (watch .git/HEAD)│ │
        │  │  • Build success (file system)  │ │
        │  │  • Timer (user-configured)      │ │
        │  │  • Manual trigger (CLI)         │ │
        │  └────────┬────────────────────────┘ │
        │           │                          │
        │           ▼                          │
        │  ┌─────────────────────────────────┐ │
        │  │  Queue State Manager            │ │
        │  │  • Load queue for display       │ │
        │  │  • Mark as read/dismissed       │ │
        │  │  • Persist state                │ │
        │  └─────────────────────────────────┘ │
        │                                       │
        └───────────────────┬───────────────────┘
                            │
                            │ (IPC / stdout)
                            │
                            ▼
                    ┌──────────────────┐
                    │   TUI Process    │
                    │  (Terminal UI)   │
                    │                  │
                    │ ┌──────────────┐ │
                    │ │ Task Display │ │
                    │ │ Keyboard I/O │ │
                    │ │ State Sync   │ │
                    │ └──────────────┘ │
                    └──────────────────┘
                            │
                            ▼
                        Developer
```

---

## Data Flow: From Notification to Display

### Flow 1: New GitHub Notification Arrives

```
1. GitHub Action
   └─> New PR opened: "Add OAuth2 support"
       Author: Sarah
       URL: github.com/myorg/auth-service/pull/456

2. Webhook Delivery (GitHub → Daemon)
   └─> POST /github/webhook
       Headers: X-GitHub-Event: pull_request
       Body: { "action": "opened", "pull_request": {...} }

3. Daemon Receives & Validates
   └─> GitHub Listener catches webhook
       Validates GitHub signature
       Extracts: PR ID, author, title, URL
       Creates Notification object

4. Deduplicate
   └─> Check: Is PR#456 already in queue?
       No? → Add to queue
       Yes? → Update existing, don't duplicate

5. Summarize
   └─> Pass to Ollama:
       "Summarize: Sarah opened PR #456 for auth-service.
        Add OAuth2 support. 3 files changed, +120 -45 lines.
        Summary (max 15 words):"
       
       Ollama responds:
       "[PR] Sarah: OAuth2 auth service (3 files, +120 -45)"

6. Store in Queue
   └─> Queue object:
       {
         "id": "github-pr-456",
         "summary": "[PR] Sarah: OAuth2 auth service...",
         "url": "github.com/myorg/auth-service/pull/456",
         "timestamp": 1694000000,
         "read": false
       }

7. Persist to Disk
   └─> Write queue.json with updated notifications

8. Wait for Release Trigger
   └─> (At this point, nothing shows up yet)
```

### Flow 2: Release Trigger Fires

```
1. Developer Commits Code
   └─> $ git commit -m "Fix bug in logger"
       Daemon detects: .git/HEAD changed

2. Release Logic Evaluates
   └─> Is there a pending commit/build event? Yes
       Should we show tasks now? Check rules:
       - Is queue non-empty? Yes (1 PR waiting)
       - Is release timer expired? Yes
       - Is developer in focus mode? No
       → TRIGGER RELEASE

3. TUI Receives Signal
   └─> IPC message from daemon:
       "SHOW_QUEUE_NOW"

4. TUI Loads Queue & Displays
   └─> Read queue.json
       Render:
       ═══════════════════════════════
       CONTEXT QUEUE [1 task waiting]
       ═══════════════════════════════
       [1] [PR] Sarah: OAuth2 auth service (3 files, +120 -45)
       
       Press: [ENTER] to check | [d] to defer | [ESC] exit

5. Developer Interacts
   └─> Presses ENTER
       → Opens browser to PR URL
       → Reads details (full PR, code diff)
       → Decides: "Review now" or "Review later"

6. Mark as Read/Dismissed
   └─> Developer presses 'd' to defer
       Daemon marks: notification.read = true
       Persists to queue.json
       Removes from immediate display

7. Back to Focus
   └─> TUI exits
       Daemon continues listening
       Developer returns to code
```

---

## Component Deep Dive

### 1. GitHub Listener

**Responsibility:** Receive, parse, validate GitHub webhooks

**How it works:**
```python
# src/github_listener.py
import json
from flask import Flask
from notification import Notification

app = Flask(__name__)

@app.route('/github/webhook', methods=['POST'])
def handle_webhook():
    payload = request.json
    event_type = request.headers.get('X-GitHub-Event')
    
    # Validate GitHub signature (optional but recommended)
    validate_github_signature(request)
    
    # Parse different event types
    if event_type == 'pull_request':
        pr = payload['pull_request']
        notif = Notification(
            source='github',
            type='pull_request',
            author=pr['user']['login'],
            title=pr['title'],
            url=pr['html_url'],
            raw_data=pr
        )
    
    elif event_type == 'issues':
        issue = payload['issue']
        notif = Notification(
            source='github',
            type='issue',
            author=issue['user']['login'],
            title=issue['title'],
            url=issue['html_url'],
            raw_data=issue
        )
    
    # Add to queue
    queue.add(notif)
    return {'status': 'ok'}, 200
```

**Configuration:**
- Listen on port `9001` (or configurable)
- Validate GitHub webhook signature (using secret)
- Handle different event types: PR, Issue, Mention

---

### 2. Notification Queue

**Responsibility:** Store, deduplicate, manage notification state

**Data Structure:**
```python
# src/notification.py
class Notification:
    def __init__(self, source, type, author, title, url, raw_data):
        self.id = f"{source}-{type}-{raw_data['id']}"  # unique key
        self.source = source  # 'github', 'slack', etc.
        self.type = type  # 'pr', 'issue', 'mention'
        self.author = author
        self.title = title
        self.url = url
        self.raw_data = raw_data
        self.timestamp = time.time()
        self.summary = None  # Set by summarizer
        self.read = False
        self.deferred = False
    
    def to_dict(self):
        return {
            'id': self.id,
            'source': self.source,
            'type': self.type,
            'author': self.author,
            'title': self.title,
            'url': self.url,
            'summary': self.summary,
            'timestamp': self.timestamp,
            'read': self.read,
            'deferred': self.deferred
        }
```

**Queue Logic:**
```python
# src/queue.py
class NotificationQueue:
    def __init__(self, file_path='queue.json'):
        self.file_path = file_path
        self.notifications = {}  # id -> Notification
        self.load()
    
    def add(self, notification):
        """Add or update notification"""
        if notification.id in self.notifications:
            # Update existing (handle duplicates)
            existing = self.notifications[notification.id]
            existing.update(notification)
        else:
            # New notification
            self.notifications[notification.id] = notification
        
        self.save()
    
    def get_pending(self):
        """Get unread, non-deferred notifications"""
        return [
            n for n in self.notifications.values()
            if not n.read and not n.deferred
        ]
    
    def load(self):
        """Load from queue.json"""
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as f:
                data = json.load(f)
                for item in data:
                    notif = Notification.from_dict(item)
                    self.notifications[notif.id] = notif
    
    def save(self):
        """Persist to queue.json"""
        with open(self.file_path, 'w') as f:
            data = [n.to_dict() for n in self.notifications.values()]
            json.dump(data, f, indent=2)
```

---

### 3. Summarizer (LLM Integration)

**Responsibility:** Call Ollama, format summaries, cache results

**How it works:**
```python
# src/summarizer.py
import requests
import json
import hashlib

class Summarizer:
    def __init__(self, ollama_url='http://localhost:11434'):
        self.ollama_url = ollama_url
        self.model = 'neural-chat'
        self.cache = {}  # id -> summary
    
    def summarize(self, notification):
        """Summarize a notification using local LLM"""
        
        # Check cache first
        cache_key = notification.id
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Build the prompt
        prompt = self._build_prompt(notification)
        
        try:
            # Call local Ollama
            response = requests.post(
                f'{self.ollama_url}/api/generate',
                json={
                    'model': self.model,
                    'prompt': prompt,
                    'stream': False
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                summary = result['response'].strip()
                
                # Cache and return
                self.cache[cache_key] = summary
                return summary
            else:
                return None
        
        except Exception as e:
            print(f"Error calling Ollama: {e}")
            return None
    
    def _build_prompt(self, notification):
        """Build the LLM prompt"""
        return f"""You are a notification summarizer for developers.
Your task: Summarize this notification in ONE line (max 15 words).

Format: [TYPE] AUTHOR: ACTION (brief context)
Examples:
[PR] Alice: Auth service ready (3 files, +120 -45)
[Issue] Bob: Deploy failed in prod (urgent)
[Mention] You were mentioned in #backend thread

Notification:
Type: {notification.type}
Author: {notification.author}
Title: {notification.title}

Summary:"""
```

**Why cache?**
- Same notification might be re-processed
- Avoid redundant LLM calls
- Faster display

---

### 4. Release Logic

**Responsibility:** Determine when to show tasks to developer

**How it works:**
```python
# src/release_logic.py
import time
import os
import subprocess
from datetime import datetime

class ReleaseLogic:
    def __init__(self, queue, tui_manager, config):
        self.queue = queue
        self.tui = tui_manager
        self.config = config
        
        self.last_git_commit_time = None
        self.last_release_time = None
        self.check_interval = config.get('check_interval_seconds', 3600)
    
    def should_release(self):
        """Determine if we should show tasks now"""
        
        # Check 1: Is there anything to show?
        if not self.queue.get_pending():
            return False
        
        # Check 2: Has enough time passed since last release?
        if self.last_release_time:
            time_since_release = time.time() - self.last_release_time
            if time_since_release < self.check_interval:
                return False
        
        # Check 3: Did a git commit just happen?
        if self._git_commit_detected():
            return True
        
        # Check 4: Did a build just succeed?
        if self._build_success_detected():
            return True
        
        # Check 5: Manual trigger (from CLI)?
        if self.config.get('manual_trigger'):
            return True
        
        return False
    
    def _git_commit_detected(self):
        """Check if .git/HEAD was recently updated"""
        try:
            git_head = os.path.getmtime('.git/HEAD')
            
            if self.last_git_commit_time is None:
                self.last_git_commit_time = git_head
                return False
            
            if git_head > self.last_git_commit_time:
                self.last_git_commit_time = git_head
                return True
        
        except Exception:
            pass
        
        return False
    
    def _build_success_detected(self):
        """Check for build success (simple heuristic)"""
        # For demo: watch for specific files
        build_marker = '.build_success_recent'
        if os.path.exists(build_marker):
            # File exists = recent build success
            os.remove(build_marker)
            return True
        return False
    
    def trigger_release(self):
        """Show tasks to user"""
        pending = self.queue.get_pending()
        self.tui.show_queue(pending)
        self.last_release_time = time.time()
```

**Configuration (in config.json):**
```json
{
  "check_interval_seconds": 3600,
  "git_commit_trigger": true,
  "build_success_trigger": true,
  "manual_trigger_enabled": true,
  "auto_clear_read_after_days": 7
}
```

---

### 5. Terminal UI (TUI)

**Responsibility:** Display queue, handle keyboard input, show task details

**Libraries:**
- **Rich** (Python): Beautiful terminal formatting
- **Interactive UI components:** Tables, panels, buttons

**How it looks:**
```
╭──────────────────────────────────────────────────────────────╮
│                   CONTEXT QUEUE                              │
│                   [3 tasks waiting]                          │
├──────────────────────────────────────────────────────────────┤
│  [1] 🔵 [PR]      Sarah: OAuth2 auth service (+120 -45)     │
│  [2] ⚠️  [Issue]   Deploy timeout in prod (urgent)           │
│  [3] 🟡 [Mention] Team question in #backend                 │
├──────────────────────────────────────────────────────────────┤
│  Controls: ↑↓ Navigate | ENTER Check | d Defer | x Dismiss  │
╰──────────────────────────────────────────────────────────────╯
```

**Code:**
```python
# src/tui.py
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

class TUI:
    def __init__(self, queue):
        self.queue = queue
        self.console = Console()
        self.selected_index = 0
    
    def show_queue(self, notifications):
        """Display pending notifications"""
        
        # Create table
        table = Table(title="CONTEXT QUEUE")
        table.add_column("Index", width=5)
        table.add_column("Type", width=8)
        table.add_column("Summary", width=50)
        
        for i, notif in enumerate(notifications):
            style = "bold" if i == self.selected_index else ""
            table.add_row(
                str(i + 1),
                f"[{notif.type.upper()}]",
                notif.summary,
                style=style
            )
        
        # Display
        self.console.print(table)
        self.console.print("\nControls: ↑↓ Navigate | ENTER Check | d Defer | ESC Exit")
        
        # Handle input
        self._handle_input(notifications)
    
    def _handle_input(self, notifications):
        """Listen for keyboard input"""
        while True:
            key = self._get_key()
            
            if key == 'up':
                self.selected_index = max(0, self.selected_index - 1)
            elif key == 'down':
                self.selected_index = min(len(notifications) - 1, self.selected_index + 1)
            elif key == 'enter':
                selected = notifications[self.selected_index]
                self._open_url(selected.url)
            elif key == 'd':
                selected = notifications[self.selected_index]
                selected.deferred = True
                self.queue.save()
            elif key == 'esc':
                break
            
            self.show_queue(notifications)
```

---

## Execution Flow (24-Hour Timeline Mapping)

### Hour 0-2: Setup
- Person 1 initializes GitHub webhook listener structure
- Person 2 tests local Ollama instance
- Person 4 sets up daemon skeleton, configuration loading

### Hour 2-6: Core Integration
- GitHub listener captures real webhooks
- Notifications added to queue
- LLM summarization working (crude summaries initially)
- Queue persisting to JSON

### Hour 6-12: Refinement
- Summarizer prompts tuned by Person 2
- Release logic detects git commits
- TUI displays queue cleanly
- Manual trigger works

### Hour 12-18: End-to-End Testing
- Full flow: webhook → summarize → persist → display
- Edge case handling (missing author, long titles, etc.)
- Performance testing (how fast is summarization?)

### Hour 18-24: Polish & Demo
- Bug fixes
- Live demo with real GitHub notifications
- Presentation prep

---

## Failure Modes & Mitigation

| Failure | Symptom | Mitigation |
|---------|---------|-----------|
| **Ollama crashes** | Summaries fail silently | Fall back to default format: `[TYPE] AUTHOR: TITLE` |
| **GitHub webhook timeout** | Missing notifications | Implement polling as backup |
| **Queue corruption** | JSON parse error | Keep backup of queue.json, auto-repair |
| **TUI crashes** | Can't view queue | Store state in JSON, can view manually |
| **Git detection fails** | Never triggers release | Manual trigger always available |

---

## Performance Targets

| Operation | Target | Current |
|-----------|--------|---------|
| Webhook receive → queue | <100ms | ✅ Fast (network bound) |
| LLM summarization | <5sec | ✅ Acceptable for MVP |
| Queue display (TUI) | <1sec | ✅ Fast (local disk) |
| Total: webhook → display | <6sec | ✅ Acceptable |

**Optimization (if needed):**
- Batch summarization
- Pre-cache common summaries
- Use faster LLM model (orca-mini instead of neural-chat)

---

## Configuration Reference

**config.json:**
```json
{
  "github": {
    "webhook_port": 9001,
    "webhook_secret": "your-secret",
    "api_token": ""  // optional, for polling
  },
  "ollama": {
    "url": "http://localhost:11434",
    "model": "neural-chat"
  },
  "queue": {
    "file_path": "queue.json",
    "max_size": 100,
    "auto_clear_after_days": 7
  },
  "release_logic": {
    "check_interval_seconds": 3600,
    "git_commit_trigger": true,
    "build_success_trigger": true,
    "manual_trigger_enabled": true
  },
  "tui": {
    "enable": true,
    "auto_refresh_seconds": 2
  }
}
```

---

## Next Steps

1. Finalize technology choices (Python vs Node?)
2. Set up GitHub webhook (ngrok for local testing)
3. Verify Ollama works locally
4. Create skeleton project structure
5. Begin Person 1 + Person 4 implementation
