# pages/1_Matches.py
import os
import streamlit as st
import utils.path_setup  # ensures root path is available
from utils.db_connection import run_query

# sidebar
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
logo_path = os.path.join(ROOT_DIR, "logo.png")
if os.path.exists(logo_path):
    st.sidebar.image(logo_path, width=80)
else:
    st.sidebar.markdown("## 🏏 Cricbuzz LiveStats")
st.sidebar.markdown("Navigate the app below 👇")

st.title("📌 Matches Overview")

col_a, col_b = st.columns([1, 3])
with col_a:
    if st.button("🔄 Refresh Live"):
        with st.spinner("Syncing live matches..."):
            try:
                from utils.sync_live import sync_live_data

                result = sync_live_data(force=True)
                st.success(f"Synced {result['synced_matches']} matches")
            except Exception as e:
                st.error(f"Live sync failed: {e}")

matches = run_query("SELECT * FROM matches ORDER BY date DESC;")
if matches.empty:
    st.warning("No matches found. Click Refresh Live to pull from Cricbuzz.")
else:
    live_mask = matches["status"].astype(str).str.contains("LIVE", case=False, na=False)
    if live_mask.any():
        st.subheader("🔴 Live now")
        st.dataframe(matches[live_mask], use_container_width=True)

    st.subheader("All matches")
    st.dataframe(matches, use_container_width=True)
    selected = st.selectbox("Select Match ID", matches["match_id"].tolist())
    st.markdown(f"**Selected:** {selected}")
    st.write(matches[matches["match_id"] == selected])

    scores = run_query("SELECT * FROM scores WHERE match_id = :mid;", params={"mid": selected})
    if not scores.empty:
        st.subheader("Score")
        st.dataframe(scores, use_container_width=True)

