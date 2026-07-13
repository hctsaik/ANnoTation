# Annotation App 外部工具

內建網頁 Canvas 不需要此目錄。若要從 App 開啟桌面標註工具，請保留完整的
Python 虛擬環境，並將執行檔放在下列相對位置：

```text
external-tools/
├─ x-anylabeling/.venv/Scripts/xanylabeling.exe
├─ labelme/.venv/Scripts/labelme.exe
└─ isat/.venv/Scripts/isat-sam.exe
```

不要只複製單一 `.exe`；其相鄰的 Python、site-packages 與相關檔案也必須存在。
Tauri 打包時應將 `external-tools` 作為 resource/sidecar 目錄一起部署。

若工具已安裝在其他位置，可在缺檔訊息下點擊「📁 選擇 … 執行檔」。App 只會把
該電腦上的絕對路徑保存到 `module_012.json`，不會複製或上傳選取的檔案。
