import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv
import os
import urllib.parse
from datetime import datetime
import time

load_dotenv()
API_URL = os.getenv("API_URL", "http://api_service:8000")

st.set_page_config(page_title="LoL Pro Grid", layout="wide")


# --- ASSETS ---
@st.cache_data
def get_ddragon_version():
    try:
        return requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=3).json()[0]
    except:
        return "14.23.1"


VER = get_ddragon_version()


def get_champ_img(name):
    if not name: return "https://cdn.communitydragon.org/latest/champion/unknown/square"
    return f"https://cdn.communitydragon.org/latest/champion/{name}/square"


def get_profile_icon(icon_id):
    if not icon_id: icon_id = 29
    return f"https://ddragon.leagueoflegends.com/cdn/{VER}/img/profileicon/{icon_id}.png"


def get_item_img(item_id):
    if not item_id or item_id == 0: return "https://upload.wikimedia.org/wikipedia/commons/c/ca/1x1.png"
    return f"https://ddragon.leagueoflegends.com/cdn/{VER}/img/item/{item_id}.png"


def get_rank_emblem(tier):
    if not tier or tier == "UNRANKED":
        return "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-unranked.png"
    return f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-{tier.lower()}.png"


def get_queue_name(qid):
    queues = {420: "Ranked Solo", 440: "Ranked Flex", 450: "ARAM", 490: "Quickplay", 1700: "Arena", 1900: "URF"}
    return queues.get(qid, f"Queue {qid}")


# --- STATE ---
if 'current_user' not in st.session_state: st.session_state['current_user'] = None


def get_summoners():
    try:
        return requests.get(f"{API_URL}/summoners", timeout=3).json()
    except:
        return []


def try_add_summoner(name):
    try:
        r = requests.post(f"{API_URL}/add_summoner", json={"name_tag": name}, timeout=30)
        return (r.status_code == 200, r.json().get("message") if r.status_code == 200 else r.text)
    except Exception as e:
        return (False, f"Error: {str(e)}")


# --- SIDEBAR ---
with st.sidebar:
    st.title("🎮 LoL Pro")
    st.caption(f"Patch: {VER}")

    st.markdown("### 📂 Players")
    raw_list = get_summoners()
    if isinstance(raw_list, list):
        for summ in sorted(list(set(raw_list))):
            if st.button(f"👤 {summ}", key=summ):
                st.session_state['current_user'] = summ
                st.rerun()
    else:
        st.warning("Connecting to DB...")

    st.markdown("---")
    if st.button("🔄 Refresh"): st.rerun()

# --- MAIN ---
st.write("")
c1, c2 = st.columns([4, 1])
with c1:
    search = st.text_input("Search Player", value=st.session_state['current_user'] or "")
with c2:
    st.write("");
    st.write("")
    if st.button("🔍 Go", type="primary"):
        st.session_state['current_user'] = search
        st.rerun()

target = st.session_state['current_user']
if not target: st.info("👈 Select a player to start."); st.stop()

safe_name = urllib.parse.quote(target)
try:
    res = requests.get(f"{API_URL}/stats/{safe_name}", timeout=10).json()
except:
    st.error("Error connecting to API Service."); st.stop()

if 'error' in res:
    if "#" in target:
        with st.status(f"🚀 Adding **{target}**...") as status:
            ok, msg = try_add_summoner(target)
            if ok:
                status.write("✅ Player found!")
                time.sleep(1.5)
                st.rerun()
            else:
                status.update(label="Error", state="error")
                st.error(msg);
                st.stop()
    else:
        st.error("Player not found.");
        st.stop()

matches = res.get('matches', [])
agg = res.get('aggregated', [])

# --- GENERAL WINRATE CALCULATION ---
total_games = len(matches)
total_wins = sum(1 for m in matches if m['win'])
general_wr = (total_wins / total_games * 100) if total_games > 0 else 0
wr_color = "#5383e8" if general_wr >= 50 else "#e84057"

# --- HEADER ---
c_prof, c_inf, c_rank = st.columns([1, 3, 2])
with c_prof:
    icon_url = get_profile_icon(res.get('profile_icon', 29))
    level = res.get('level', 0)
    st.markdown(f"""
        <div style='text-align:center'>
            <img src='{icon_url}' style='width:90px; border-radius:20px; border:2px solid #d4af37; box-shadow: 0 0 15px rgba(0,0,0,0.6);'>
            <br><span style='background:#111; padding:2px 8px; border-radius:4px; font-size:0.9em; border:1px solid #333;'>Lvl {level}</span>
        </div>
    """, unsafe_allow_html=True)

with c_inf:
    st.title(res.get('summoner'))
    st.markdown(f"""
        <div style="font-size:1.2em; margin-top:-15px;">
            <span style="color:{wr_color}; font-weight:bold;">{general_wr:.0f}% WR</span> 
            <span style="color:#888; font-size:0.9em;">({total_wins}W - {total_games - total_wins}L)</span>
            <span style="color:#666; font-size:0.8em; margin-left:10px;">Last {total_games} matches</span>
        </div>
    """, unsafe_allow_html=True)

with c_rank:
    solo = res.get('rank_solo', {})
    tier = solo.get('tier', 'UNRANKED')

    if tier != "UNRANKED":
        emblem = get_rank_emblem(tier)
        st.markdown(f"""
        <div style="display:flex; align-items:center; gap:0px;">
            <div style="width:120px; height:100px; display:flex; align-items:center; justify-content:center; overflow:visible;">
                <img src="{emblem}" style="width:120px; transform: scale(1.4);"> 
            </div>
            <div style="line-height:1.2; margin-left: 10px;">
                <div style="color:#888; font-size:0.85em; font-weight:bold; text-transform:uppercase;">SoloQ</div>
                <div style="color:#fff; font-size:1.6em; font-weight:bold;">{tier} {solo.get('rank')}</div>
                <div style="color:#ccc; font-size:1.1em;">{solo.get('lp')} LP</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="display:flex; align-items:center; gap:15px; opacity:0.5;">
            <div style="width:100px; text-align:center; font-size:3em;">?</div>
            <div>
                <div style="font-weight:bold;">SoloQ</div>
                <div>Unranked</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("---")

st.markdown("""
<style>
    .match-card { background-color: #18181b; border-radius: 5px; padding: 5px; margin-bottom: 5px; border-left: 4px solid #333; }
    .win { border-left-color: #5383e8; background: rgba(40,52,78,0.3); }
    .loss { border-left-color: #e84057; background: rgba(59,42,46,0.3); }
    .kda-main { font-weight:bold; font-size:1em; }
    .meta { font-size:0.75em; color:#888; }
    .item-icon { width:20px; height:20px; border-radius:3px; border:1px solid #222; }
    .player-row { display: flex; justify-content: space-between; font-size: 0.8em; padding: 2px; border-bottom: 1px solid #222; }
</style>
""", unsafe_allow_html=True)

tab_hist, tab_stats = st.tabs(["📜 Match History", "🏆 Stats"])

with tab_hist:
    t_all, t_solo, t_flex, t_aram = st.tabs(["All", "SoloQ", "Flex", "ARAM"])


    def render_list(filtered):
        if not filtered:
            st.info("No matches found.")
            return

        cols = st.columns(2)
        for i, m in enumerate(filtered):
            with cols[i % 2]:
                win = m['win']
                css = "win" if win else "loss"
                result_text = "Victory" if win else "Defeat"
                color = "#5383e8" if win else "#e84057"

                with st.container():
                    st.markdown(f"<div class='match-card {css}'>", unsafe_allow_html=True)
                    c1, c2, c3 = st.columns([1, 2, 1.5])
                    with c1: st.image(get_champ_img(m['champion']), width=45)
                    with c2:
                        st.markdown(f"<div class='kda-main'>{m['kills']}/{m['deaths']}/{m['assists']}</div>",
                                    unsafe_allow_html=True)
                        st.markdown(f"<div class='meta'>KDA {m['kda']} • CS {m['cs']}</div>", unsafe_allow_html=True)
                        items_html = "".join(
                            [f"<img src='{get_item_img(it)}' class='item-icon'>" for it in m.get('items', []) if it])
                        st.markdown(f"<div style='margin-top:2px'>{items_html}</div>", unsafe_allow_html=True)
                    with c3:
                        st.markdown(f"<div style='color:{color}; font-weight:bold'>{result_text}</div>",
                                    unsafe_allow_html=True)
                        ts = m.get('game_timestamp', 0)
                        dt = datetime.fromtimestamp(ts / 1000).strftime('%d/%m') if ts else ""
                        q_name = get_queue_name(m.get('queue_id', 0))
                        dur = f"{int(m['cs'] / m['cs_min'])}m" if m.get('cs_min') else ""
                        st.markdown(f"<div class='meta'>{dt} ({dur})</div>", unsafe_allow_html=True)
                        st.markdown(f"<div class='meta'>{q_name}</div>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                with st.expander("🔎 Details"):
                    parts = m.get('participants', [])
                    if parts:
                        col_b, col_r = st.columns(2)
                        with col_b:
                            st.caption("Blue Team")
                            for p in parts:
                                if p.get('teamId') == 100:
                                    is_me = p.get('summonerName') in res.get('summoner')
                                    b = "font-weight:bold; color:#fff;" if is_me else ""
                                    p_items = "".join(
                                        [f"<img src='{get_item_img(it)}' width='16' style='margin-left:1px'>" for it in
                                         p.get('items', []) if it])
                                    p_img = get_champ_img(p.get('champion'))
                                    st.markdown(
                                        f"""<div class='player-row'><div style='display:flex; align-items:center; gap:5px;'><img src='{p_img}' width='20' style='border-radius:50%'><span style='{b}'>{p.get('summonerName')}</span></div><div style='text-align:right'><span>{p.get('kills')}/{p.get('deaths')}/{p.get('assists')}</span><br>{p_items}</div></div>""",
                                        unsafe_allow_html=True)
                        with col_r:
                            st.caption("Red Team")
                            for p in parts:
                                if p.get('teamId') == 200:
                                    is_me = p.get('summonerName') in res.get('summoner')
                                    b = "font-weight:bold; color:#fff;" if is_me else ""
                                    p_items = "".join(
                                        [f"<img src='{get_item_img(it)}' width='16' style='margin-left:1px'>" for it in
                                         p.get('items', []) if it])
                                    p_img = get_champ_img(p.get('champion'))
                                    st.markdown(
                                        f"""<div class='player-row'><div style='display:flex; align-items:center; gap:5px;'><img src='{p_img}' width='20' style='border-radius:50%'><span style='{b}'>{p.get('summonerName')}</span></div><div style='text-align:right'><span>{p.get('kills')}/{p.get('deaths')}/{p.get('assists')}</span><br>{p_items}</div></div>""",
                                        unsafe_allow_html=True)


    with t_all:
        render_list(matches)
    with t_solo:
        render_list([m for m in matches if m.get('queue_id') == 420])
    with t_flex:
        render_list([m for m in matches if m.get('queue_id') == 440])
    with t_aram:
        render_list([m for m in matches if m.get('queue_id') == 450])

with tab_stats:
    if agg:
        st.subheader("Top Champions")
        df = pd.DataFrame(agg).sort_values('games', ascending=False)
        df['Img'] = df['champion'].apply(get_champ_img)
        st.dataframe(
            df[['Img', 'champion', 'games', 'winrate', 'avg_kda']],
            column_config={"Img": st.column_config.ImageColumn(""),
                           "winrate": st.column_config.NumberColumn("WR%", format="%.1f")},
            use_container_width=True, hide_index=True
        )
    else:
        st.info("No data available.")