"""Help for the preserved local annotation workflow."""


def render_help_button(module_id, section, title):
    import streamlit as st
    with st.expander(title, expanded=False):
        st.write("先在資料來源選擇圖片資料夾，建立資料集，再到標注工作台使用網頁編輯器儲存。")
        st.write("標注存於圖片旁的同名 JSON；原圖不改寫。審核結果另存 .review.json。")
        st.write("完成審核後選擇匯出格式及輸出目錄。資料集清單與設定保存在 App 私有資料目錄。")
        st.caption("獨立版本提供本機流程；CIM 外部任務系統需另行配置公開服務接入。AI 預標及外部桌面工具需自行準備。")
