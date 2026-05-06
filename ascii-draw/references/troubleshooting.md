# Troubleshooting — ascii-draw

`launch.ps1` が `error` を返したとき、または起動後にユーザーが症状を訴えたときの対処集。

## 起動エラー (launch.ps1 の status=error)

### `'py' command not found`

**原因**: Python が PATH に通っていない、または python.org 公式版でないため `py` ランチャーが入っていない。

**対処**:
1. `winget install Python.Python.3.12` で Python 公式版を入れる
2. PowerShell を再起動 → `py -V` でバージョンが出ることを確認
3. もう一度 launch.ps1 を実行

### `server.py not found at: <skill-root>\scripts\server.py`

**原因**: ツールディレクトリが移動・削除されているか、レイアウトが旧版 (root 直下 server.py) のまま。

**対処**:
1. `Test-Path "$PSScriptRoot\server.py"` (= `<skill>\scripts\server.py`) で存在確認
2. スキルフォルダごと欠落している場合は再配置（self-contained なのでフォルダさえあれば動く）
3. 旧レイアウト（server.py が root 直下）に置き換えられている場合は、最新版を `install.ps1 -Force` で再インストール

### `Server process exited prematurely`

**原因**: `server.py` 起動直後にクラッシュ (典型的にはポート 8765 が別プロセスに占有されているか、Python ライブラリ不足)。

**対処**:
1. 最小化された PowerShell ウィンドウを開いて traceback を読む
2. ポート占有確認: `Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue`
3. 別プロセスがいたら停止: `Stop-Process -Id <PID> -Force`
4. もう一度 launch.ps1

### `Server did not respond on port 8765 within 8000ms`

**原因**: サーバ起動はしたが応答が遅い (古い Windows / Defender スキャン中 / ファイアウォール許可ダイアログが出ている)。

**対処**:
1. タスクトレイ / 最小化ウィンドウにファイアウォール確認ダイアログが無いか
2. `WaitMs` を伸ばして再試行: `powershell -File launch.ps1 -WaitMs 20000`
3. それでもダメなら手動起動して問題切り分け: `<skill-root>\scripts\start.bat` をダブルクリック、または `cd <skill-root>; py scripts\server.py`

---

## アプリ起動後の症状

### 「タイムアウト (240秒)」「ずっと処理中」

**原因**: 過去のセッションで殺し切れなかった `claude.exe` ゾンビプロセスが残っていて、新しい Claude CLI 呼び出しがロックされている。

**対処**:
```powershell
Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force
```
そのうえでブラウザで F5、もう一度生成。

複数日分溜まっていることがあるので、症状が頻発するなら定期的に上記コマンドを叩く。

### 日本語が文字化け / 罫線がガタつく

**原因 1**: 「表示」グループの `Unicode罫線` がオフになっている。

**対処**: ヘッダのチェックボックスをオンに → 自動再描画。

**原因 2**: ブラウザのフォント設定で等幅フォントが日本語非対応のものになっている。

**対処**: アプリは `ui-monospace, "Cascadia Mono", "Consolas", "MS Gothic"` の順でフォールバックする。Windows 標準 OS なら問題ないはず。Edge / Chrome の設定でフォントを変えていなければ自動で MS Gothic が当たる。

### 図が右にズレる / 列が揃わない

**原因**: 全角/半角混在で AI が誤って 1 セル幅を計算したケース。

**対処**:
1. ズレている部分を `V` キーで範囲選択
2. ヘッダの「選択シフト」 ◄▲▼► で 1 セルずつ調整
3. `Shift+クリック` で 4 セル単位、または矢印キーで微調整

### 「📝 修正」を押したら全体が書き換わってしまった

**原因**: 範囲選択せずに修正ボタンを押したため、全体修正モードで動いた。

**対処**:
1. `Ctrl+Z` で元に戻す
2. `V` キーで直したい箱所だけを選択 → ボタンが「📝 選択範囲のみ修正」に変わるのを確認 → 入力 → クリック
3. ボタン文字が「📝 修正」(全体) のままなら範囲選択ができていない

### ブラウザに「Directory listing for /」が出る

**原因**: 別の `python -m http.server` が同じポートで動いている (古い起動の残骸)。

**対処**:
```powershell
Get-NetTCPConnection -LocalPort 8765 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```
そのうえで launch.ps1 を再実行。

### PNG 出力が小さい / ぼやける

**原因**: 出力済み画像が CPP ペースト時に縮小された。

**対処**: PNG 出力は **3× 解像度** で生成済み。PowerPoint に貼ったあとに「サイズの再設定」で元寸に戻すか、貼り付け時に右クリック → 「画像として貼り付け」を選ぶ。

### AI 応答が遅い (1〜3 分かかる)

**仕様**:
- 初回: Claude CLI のセッション起動コストで 30〜90 秒
- 2回目以降: 普通は 15〜45 秒
- 複雑な図 (50 ノード超など): 1〜3 分

`server.py` は最大 15 分まで待つ設定。それを超えたら本当のタイムアウトなのでもう一度試す。

---

## 「もう一度クリーンに起動したい」 (nuclear option)

```powershell
# 1. 全ての関連プロセスを止める
Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# 2. 改めて起動
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "<skill-root>\scripts\launch.ps1"
```
