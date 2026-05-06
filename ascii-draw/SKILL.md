---
name: ascii-draw
description: "ローカル ASCII Draw エディタを起動する。Claude Code のサブスクリプション経由で AI 生成し、Unicode罫線で構成図/フロー図/シーケンス図/ER図/状態遷移図/プロセス図などを作成し、PPT用に3×解像度PNGで書き出すための Win 用 Web ツール。Use when the user asks to draw / sketch / create / generate a diagram, architecture chart, flow chart, sequence diagram, system design, ER diagram, state machine, or ASCII art — or says 「構成図」「アーキテクチャ図」「フロー図」「シーケンス図」「ASCII で図」「PPT に貼る図」「ascii-draw 起動」."
compatibility: "windows"
user-invocable: true
license: "MIT"
metadata: "least-privilege: parent-Claude needs Bash(powershell launch.ps1) + Read(references). Embedded claude -p uses --tools Read,Glob,Grep only. No Write/Edit/MultiEdit/Bash-arbitrary/WebFetch/WebSearch/Task. Diagram generation never modifies user files."
---

# ASCII Draw

ローカル Web エディタ (`http://127.0.0.1:8765`) を起動し、ユーザーがブラウザ上で AI 生成 + 手調整 + PNG エクスポートできる状態にする。

## このスキルは self-contained

スキルフォルダ自身に runtime も含まれている。**絶対パスへの依存は一切なし** — `${CLAUDE_SKILL_DIR}` (Claude Code が解決) と `$PSScriptRoot` (PowerShell が解決) で全てのパスが相対的に決まる。

レイアウトは [Anthropic 公式 Skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) に準拠：root 直下は manifest のみ、実行ファイルは `scripts/`、静的アセットは `assets/`、ドキュメントは `references/`。

```
<skill-root>/                  ← 中身ごと配置すれば動く（場所問わず）
├── SKILL.md                   ← 必須 manifest（root 直下はこれと LICENSE のみ）
├── scripts/                   ← Claude / ユーザーが「実行」するファイル
│   ├── launch.ps1             ← Claude Code から呼ぶランチャ
│   ├── server.py              ← HTTP サーバ + Claude CLI ブリッジ
│   ├── start.bat              ← 手動起動 (ダブルクリック用)
│   ├── install.ps1            ← ~/.claude/skills/ へインストール（admin不要）
│   └── uninstall.ps1          ← クリーンアンインストール
├── assets/                    ← 実行時に「参照される」静的アセット
│   └── index.html             ← Canvas エディタ UI（GET / で配信）
└── references/                ← 必要時にだけ読み込む docs
    ├── prompt-cookbook.md     ← AI プロンプトのベストプラクティス
    └── troubleshooting.md     ← エラー別対処
```

- **Server URL**: `http://127.0.0.1:8765` (起動時に自動でブラウザを開く)
- **In-app help**: アプリ内で `F1` キー / `?` キー / 右上「? ヘルプ」ボタン
- **Architecture**: `assets/index.html` (canvas エディタ) ⇄ `scripts/server.py` (Claude CLI ブリッジ) ⇄ `claude -p` (Opus, `--effort low`, `--no-session-persistence`)
- **External requirements**: `py` (Python ランチャー、python.org 公式版に同梱) と `claude` (Claude Code CLI) のみ。**管理者権限不要**。

## When to invoke this skill

以下のような依頼が来たら **必ず** このスキルを起動する：

| ユーザー発言の例 | 起動する |
|----------------|---------|
| 「構成図を描いて」「アーキテクチャ図を作って」 | ✅ |
| 「フロー図」「シーケンス図」「ER 図」「状態遷移図」 | ✅ |
| 「PPT に貼る図を作りたい」「ASCII で〜の図」 | ✅ |
| 「ascii-draw 起動」「`/ascii-draw`」 | ✅ |
| 既存の図を直したい (修正は **アプリ内の範囲選択 → 「📝 選択範囲のみ修正」** で行う) | ✅ |
| 数行の単純なリストやテキスト整形 (アプリは過剰) | ❌ |
| Mermaid / draw.io で十分な、Web 出力前提のもの | ❌ |

> ⚠️ **Claude が会話内で罫線の図を直接「描かない」**。LLM はセル単位の整列を保証できないので、専用エディタ (このツール) に委譲する。これがツールを作った最大の理由。

## Activation procedure (runtime)

### Step 1 — Launch via PowerShell script

`scripts/launch.ps1` がポート確認 → 未起動なら起動 → ready 待機 → JSON で結果を返す。

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_SKILL_DIR}/scripts/launch.ps1"
```

`${CLAUDE_SKILL_DIR}` はスキル登録後に Claude Code が自動展開する（このスキルがインストールされていれば常に正しいパスになる）。スキル外で実行する場合は launch.ps1 のフルパスを直接指定する。

### Step 2 — Parse the JSON output and report

スクリプトは1行の JSON を返す：

| status | 意味 | ユーザーへの報告 |
|--------|------|---------------|
| `already_running` | 既に起動済み | 「✓ ASCII Draw は既に起動中 → http://127.0.0.1:8765」 |
| `started` | 新規起動成功 | 「✓ ASCII Draw を起動しました → http://127.0.0.1:8765」 |
| `error` | 失敗 | `reason` フィールドの内容を伝え、`references/troubleshooting.md` を参照 |

成功時は **2行以内** で完結させる。例：

> ✓ ASCII Draw を起動しました → **http://127.0.0.1:8765**
> ブラウザが自動で開かない場合は上の URL を開いてください。入力欄に作りたい図を日本語で書いて Enter / 「✨ 生成」を押すだけ。詳細は画面右上の「? ヘルプ」(F1) に。

### Step 3 — Suggest a prompt (optional, conditional)

ユーザーが **このスキル呼出と同じターン** で具体的な図の話をしていた場合 (例: 「OAuth 認可コードフローの図を ascii-draw で」) **のみ**：

1. `references/prompt-cookbook.md` を読む
2. 該当カテゴリのテンプレートを参考に、最適なプロンプト案を **1〜2 個** 提示
3. 「↑入力欄にコピペして Enter で生成されます」と添える

呼出だけでテーマが特定されていない場合は **何も提案しない**。アプリ内ヘルプが扱う。

## Critical rules

- ✅ 起動が完了するまで「起動しました」と伝えない (launch.ps1 は ready 待機を内蔵しているが念のため)
- ✅ サーバ既起動時は再起動しない (`already_running` を尊重)
- ✅ 起動成功後の追加機能説明は **しない** (アプリ内 F1 ヘルプが完璧にカバー)
- ❌ `python` / `python3` コマンドは使わない (この PC は `py` のみ)
- ❌ 会話内で同じ図を ASCII で書いて見せない (ツールに任せる)
- ❌ ユーザーが範囲選択せずに「ここを直して」と言った場合、勝手に全体修正に走らない — 「画面で `V` キー → 直したい箱所をドラッグ → 入力欄に修正内容 → 📝 選択範囲のみ修正 を押してください」と案内する

## Tool usage policy (least-privilege)

**設計方針**: このスキルが必要とする権限は **読み取りと PowerShell 起動のみ**。ユーザーのファイルを書き換えない・任意コマンドを実行しない・ネットワークに出ない。

### Embedded `claude -p` (server.py が AI 生成のために起動する子プロセス)

`scripts/server.py` 内で **明示的に最小化済み**：

```python
"--tools", "Read,Glob,Grep",          # 読み取り3種のみ
"--allowed-tools", "Read,Glob,Grep",  # 同上を auto-approve（対話プロンプトを抑制）
```

書込・編集・任意 Bash 実行・Web アクセスは **全て不可**。AI はリポジトリ内のファイルを読めるが、改変は一切できない。

## References (load on demand)

| File | When to read |
|------|------------|
| `references/prompt-cookbook.md` | ユーザーが特定の図 (OAuth フロー / マイクロサービス構成 等) のプロンプトを欲しがったとき |
| `references/troubleshooting.md` | `launch.ps1` が `error` を返したとき / ユーザーが「タイムアウト」「文字化け」「図がずれる」等を訴えたとき |

毎回全部読む必要はない。**必要になってから** 読み込むこと。

---

## Setup

このスキルを Claude Code から認識させるには、フォルダを `~/.claude/skills/ascii-draw/` に **コピー** するだけ。symlink もジャンクションも管理者権限も不要。**会社の制限された Windows でも動く**。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "<source>\scripts\install.ps1" -Force
```

`<source>` は git clone したフォルダ。`install.ps1` が `$env:USERPROFILE\.claude\skills\ascii-draw\` へ Copy-Item します。

### Uninstall

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\uninstall.ps1" -Force
```

ポート 8765 で起動中のサーバも停止してから削除する。
