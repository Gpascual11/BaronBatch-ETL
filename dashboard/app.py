import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv
import os
import urllib.parse

load_dotenv()

API_URL = os.getenv("API_URL", "http://api_service:8000")

st.set_page_config(page_title="LoL ETL Dashboard", layout="wide")

# --- GESTIÓ DE L'ESTAT (SESSION STATE) ---
if 'current_user' not in st.session_state:
    st.session_state['current_user'] = None


# --- FUNCIONS AUXILIARS ---
def get_existing_summoners():
    """Obté la llista de noms de jugadors ja monitoritzats"""
    try:
        res = requests.get(f"{API_URL}/summoners", timeout=2)
        if res.status_code == 200:
            return res.json()
    except:
        return []
    return []


def try_add_summoner(name_tag):
    """Intenta afegir un jugador nou via API"""
    try:
        res = requests.post(f"{API_URL}/add_summoner", json={"name_tag": name_tag}, timeout=10)
        if res.status_code == 200:
            return True, res.json().get("message")
        elif res.status_code == 404:
            return False, "Jugador no trobat a Riot Games (Revisa el Nom#Tag)"
        else:
            return False, f"Error del servidor: {res.text}"
    except Exception as e:
        return False, f"Error de connexió: {e}"


# --- BARRA LATERAL (LLISTA RÀPIDA) ---
with st.sidebar:
    st.title("🎮 La Meva Llista")

    # Recuperem la llista bruta
    raw_list = get_existing_summoners()

    # 🛠️ FIX: DEDUPLICACIÓ
    # Convertim a 'set' per esborrar duplicats i ordenem alfabèticament
    existing_list = sorted(list(set(raw_list))) if raw_list else []

    if existing_list:
        st.caption(f"Tens {len(existing_list)} jugadors monitoritzats.")
        st.markdown("### 📂 Carregats:")
        for summ in existing_list:
            # Ara 'summ' és únic, així que la 'key' també ho serà
            if st.button(f"👤 {summ}", key=f"btn_{summ}"):
                st.session_state['current_user'] = summ
                st.rerun()
    else:
        st.info("La llista està buida. Fes la teva primera cerca!")

    st.markdown("---")
    if st.button("🔄 Refrescar Llista"):
        st.rerun()

# --- PANELL PRINCIPAL (CERCA UNIFICADA) ---
st.title("⚔️ League of Legends Analytics")

# 1. EL CERCADOR UNIFICAT
col_search, col_btn = st.columns([4, 1])
with col_search:
    search_input = st.text_input("Cerca un jugador (si no existeix, s'afegirà automàticament):",
                                 placeholder="Ex: Faker#KR1",
                                 value=st.session_state['current_user'] if st.session_state['current_user'] else "")

with col_btn:
    st.write("")  # Espaiat visual per alinear el botó
    st.write("")
    if st.button("🔍 Cercar / Afegir", type="primary"):
        st.session_state['current_user'] = search_input
        st.rerun()

# 2. LÒGICA DE VISUALITZACIÓ
target_summoner = st.session_state['current_user']

if not target_summoner:
    st.info("👋 Benvingut! Escriu un Riot ID (Nom#Tag) dalt per començar.")
    st.stop()

# 3. PROCÉS DE CÀRREGA INTEL·LIGENT
# A) Comprovem si ja el tenim a la llista local (Deduplicada)
# Tornem a cridar la funció deduplicada per ser consistents
existing_list = sorted(list(set(get_existing_summoners())))
is_known = target_summoner in existing_list

# B) Si NO és conegut, l'intentem afegir automàticament
if not is_known:
    if "#" not in target_summoner:
        st.warning("⚠️ Format incorrecte. Has d'incloure el Tag (Exemple: Nom#Tag)")
        st.stop()

    with st.status(f"🕵️‍♂️ Jugador nou detectat: {target_summoner}") as status:
        status.write("Connectant amb Riot Games...")
        success, msg = try_add_summoner(target_summoner)

        if success:
            status.write("✅ Jugador trobat i afegit a la base de dades.")
            status.write("🚀 Despertant l'extractor de dades...")
            status.update(label="Tot llest! Carregant estadístiques...", state="complete", expanded=False)
            st.rerun()
        else:
            status.update(label="❌ Error afegint jugador", state="error")
            st.error(msg)
            st.stop()

# C) Si és conegut, mostrem les dades
safe_name = urllib.parse.quote(target_summoner)
url = f"{API_URL}/stats/{safe_name}"

try:
    res = requests.get(url, timeout=5).json()
except Exception as e:
    st.error("Error connectant amb l'API de lectura.")
    st.stop()

if 'error' in res:
    st.warning(f"⏳ El jugador **{target_summoner}** està a la cua de monitorització.")
    st.info("L'extractor està treballant en segon pla. Torna a cercar en 1 minut.")
    if st.button("Torna-ho a provar ara"):
        st.rerun()
else:
    # --- RENDERITZAT DEL DASHBOARD ---
    real_name = res.get('summoner')
    st.markdown(f"### 📊 Estadístiques de: **{real_name}**")

    matches = res.get('matches', [])
    agg = res.get('aggregated', [])

    if matches:
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        last = matches[0]

        wins = sum(1 for m in matches if m['win'])
        wr = (wins / len(matches)) * 100

        kpi1.metric("Winrate Recent", f"{wr:.0f}%", delta_color="normal")
        kpi2.metric("Últim Campió", last['champion'])
        kpi3.metric("KDA Última", f"{last['kda']}")
        kpi4.metric("CS/min Última", f"{last['cs_min']}")

    tab_history, tab_analysis = st.tabs(["📜 Historial de Partides", "🧠 Anàlisi de Campions"])

    with tab_history:
        if matches:
            df = pd.DataFrame(matches)
            st.dataframe(
                df[['champion', 'win', 'kills', 'deaths', 'assists', 'kda', 'cs', 'cs_min', 'timestamp']]
                .style.format({'kda': '{:.2f}', 'cs_min': '{:.1f}'})
                .background_gradient(subset=['kda'], cmap="Greens"),
                use_container_width=True
            )
        else:
            st.info("Encara no hi ha partides baixades.")

    with tab_analysis:
        if agg:
            col_table, col_chart = st.columns([1, 2])
            df_agg = pd.DataFrame(agg).sort_values('games', ascending=False)

            with col_table:
                st.dataframe(df_agg[['champion', 'games', 'winrate', 'avg_kda']], use_container_width=True)

            with col_chart:
                top = df_agg.head(7)
                if not top.empty:
                    fig, ax = plt.subplots()
                    ax.barh(top['champion'], top['winrate'], color='skyblue')
                    ax.set_xlabel("Winrate (%)")
                    ax.set_xlim(0, 100)
                    ax.axvline(50, color='red', linestyle='--', alpha=0.5)
                    st.pyplot(fig)
        else:
            st.info("Dades insuficients per a l'anàlisi agregada.")