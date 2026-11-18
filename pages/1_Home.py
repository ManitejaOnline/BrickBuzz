# pages/1_Home.py
import streamlit as st
import os

# --- Sidebar (set once globally in Streamlit_app.py, so no st.set_page_config here) ---
st.sidebar.image(
    "https://cdn-icons-png.flaticon.com/512/861/861512.png", 
    width=60
)
st.sidebar.title("🏏 Cricbuzz LiveStats")
st.sidebar.markdown("Navigate through the app using the menu below 👇")

# --- Try loading local logo ---
logo_path = "logo.png"  # make sure it's in the project root
if os.path.exists(logo_path):
    st.image(logo_path, width=120, caption="Cricbuzz LiveStats")
else:
    st.warning("⚠️ Logo not found (logo.png). Please check file location.")

# --- Title & Intro ---
st.title("🏏 Cricbuzz LiveStats Dashboard")
st.write("""
Welcome to your cricket analytics project!  

📌 Use the sidebar to navigate through the app:
- **Matches Overview** → see all matches stored in the database  
- **Players** → explore player info and roles  
- **Scores** → check scorecards by match  
- **SQL Queries** → run custom SQL against the cricket database  
- **CRUD Pages** → add, update, and delete records  
- **Visualizations** → interactive charts and insights  

---
Powered by: **SQLite + SQLAlchemy**, **Streamlit**, **Pandas + Plotly**, **RapidAPI Cricbuzz**
""")

st.subheader("🔴 Live data")
st.caption("Pulls live / recent / upcoming matches from Cricbuzz via RapidAPI into SQLite.")
if st.button("Refresh Live Data", type="primary"):
    with st.spinner("Fetching live matches..."):
        try:
            from utils.sync_live import sync_live_data

            result = sync_live_data(force=True)
            st.success(
                f"Synced {result['synced_matches']} matches "
                f"(live {result['live_count']}, recent {result['recent_count']}, "
                f"upcoming {result['upcoming_count']}; detailed {result['detailed_matches']})."
            )
            if result.get("errors"):
                st.warning("Some scorecards could not be enriched (quota/rate limit).")
        except Exception as e:
            st.error(f"Live sync failed: {e}")

