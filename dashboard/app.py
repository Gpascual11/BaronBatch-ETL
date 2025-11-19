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


# --- FUNCIONS AUXILIARS ---
def get_summoners_list():
    try:
        res = requests.get(f"{API_URL}/summoners", timeout=3)
        if res.status_code == 200:
            return res.json()
    except:
        return []
    return []


# --- SIDEBAR ---
with st.sidebar:
    st.title("🎮 Gestor LoL")

    # 1. SELECTOR DE JUGADORS (La Llista)
    st.subheader("👤 Jugadors Monitoritzats")
    summoner_list = get_summoners_list()

    selected_summoner = None
    if summoner_list:
        # Creem un selector. Si seleccionen un, es guarda a 'selected_summoner'
        selected_summoner = st.selectbox(
            "Selecciona per veure estadístiques:",
            options=summoner_list,
            index=0
        )
        if st.button("🔄 Actualitzar Llista"):
            st.rerun()
    else:
        st.info("No hi ha jugadors. Afegeix-ne un a sota!")

    st.markdown("---")

    # 2. AFEGIR NOU JUGADOR
    st.subheader("➕ Afegir Nou")
    new_summoner = st.text_input("Riot ID (Nom#Tag):", placeholder="Ex: Caps#1337")

    if st.button("Monitoritzar"):
        if "#" in new_summoner:
            with st.spinner(f"Connectant amb Riot Games per {new_summoner}..."):
                try:
                    res = requests.post(f"{API_URL}/add_summoner", json={"name_tag": new_summoner})
                    if res.status_code == 200:
                        st.success(res.json().get("message"))
                        st.rerun()  # Recarreguem per actualitzar la llista de dalt
                    else:
                        detail = res.json().get("detail", "Error desconegut")
                        st.error(f"Error: {detail}")
                except Exception as e:
                    st.error(f"No s'ha pogut connectar: {e}")
        else:
            st.warning("Format incorrecte. Recorda el #Tag")

# --- PANELL PRINCIPAL ---
st.title("⚔️ League of Legends Analytics")

if not selected_summoner:
    st.info("👈 Selecciona un jugador de la llista o afegeix-ne un de nou per començar.")
    st.stop()

# --- CARREGA DE DADES DEL JUGADOR SELECCIONAT ---
safe_name = urllib.parse.quote(selected_summoner)
url = f"{API_URL}/stats/{safe_name}"

try:
    res = requests.get(url, timeout=5).json()
except Exception as e:
    st.error("Error connectant amb l'API de lectura.")
    st.stop()

if 'error' in res:
    st.error(f"Error: {res['error']}")
else:
    real_name = res.get('summoner')
    st.header(f"Estadístiques de: {real_name}")

    matches = res.get('matches', [])
    agg = res.get('aggregated', [])

    # KPIs Ràpids a dalt
    if matches:
        col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
        last_game = matches[0]
        col_kpi1.metric("Últim Campió", last_game['champion'])
        col_kpi1.metric("Últim KDA", f"{last_game['kda']}")
        win_count = sum(1 for m in matches if m['win'])
        win_rate_recent = (win_count / len(matches)) * 100
        col_kpi3.metric("Winrate Recent", f"{win_rate_recent:.1f}%")

    if not matches:
        st.warning("Jugador monitoritzat, però sense partides recents baixades. Espera al proper cicle automàtic.")

    # TABS per organitzar millor
    tab1, tab2 = st.tabs(["📜 Historial", "🏆 Anàlisi de Campions"])

    with tab1:
        if matches:
            df = pd.DataFrame(matches)
            cols = ['champion', 'win', 'kills', 'deaths', 'assists', 'kda', 'cs', 'cs_min', 'timestamp']


            # Format condicional per Win/Loss
            def highlight_win(val):
                color = 'rgba(144, 238, 144, 0.2)' if val else 'rgba(255, 99, 71, 0.2)'
                return f'background-color: {color}'


            st.dataframe(
                df[cols].style.applymap(highlight_win, subset=['win'])
                .format({'kda': '{:.2f}', 'cs_min': '{:.1f}'}),
                use_container_width=True
            )

    with tab2:
        if agg:
            df2 = pd.DataFrame(agg).sort_values('games', ascending=False).reset_index(drop=True)

            col_a, col_b = st.columns([1, 2])

            with col_a:
                st.dataframe(df2, use_container_width=True)

            with col_b:
                top = df2.head(7)
                if not top.empty:
                    fig, ax1 = plt.subplots()
                    color = 'tab:blue'
                    ax1.set_xlabel('Campió')
                    ax1.set_ylabel('Winrate (%)', color=color)
                    ax1.bar(top['champion'], top['winrate'], color=color, alpha=0.6)
                    ax1.tick_params(axis='y', labelcolor=color)

                    ax2 = ax1.twinx()
                    color = 'tab:red'
                    ax2.set_ylabel('KDA Mitjà', color=color)
                    ax2.plot(top['champion'], top['avg_kda'], color=color, marker='o', linewidth=2)
                    ax2.tick_params(axis='y', labelcolor=color)
                    st.pyplot(fig)