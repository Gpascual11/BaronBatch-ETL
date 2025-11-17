import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv
import os

load_dotenv()

API_URL = os.getenv("API_URL", "http://api_service:8000")

st.set_page_config(page_title="Mini OP.GG - Light", layout="wide")
st.title("Mini OP.GG — Light Dashboard")

col1, col2 = st.columns([2,1])

with col1:
    summoner = st.text_input("Nom de l'invocador:")
    if st.button("Cercar"):
        if not summoner:
            st.warning("Introdueix un nom")
        else:
            url = f"{API_URL}/stats/{summoner}"
            try:
                res = requests.get(url, timeout=10).json()
            except Exception as e:
                st.error("No s'ha pogut contactar amb l'API")
                st.stop()

            if 'error' in res:
                st.error("Jugador no trobat")
            else:
                st.subheader(f"Estadístiques de {res.get('summoner')}")
                matches = res.get('matches', [])
                agg = res.get('aggregated', [])

                if matches:
                    df = pd.DataFrame(matches)
                    st.markdown("**Últimes partides (netes)**")
                    st.dataframe(df[['matchId','champion','win','kills','deaths','assists','kda','cs','cs_min','timestamp']].head(20))

                if agg:
                    df2 = pd.DataFrame(agg)
                    st.markdown("**Rendiment per campió (últimes partides agregades)**")
                    st.dataframe(df2.sort_values('games', ascending=False).reset_index(drop=True))

                    top = df2.sort_values('games', ascending=False).head(6)
                    if not top.empty:
                        st.markdown("**Top champions — Winrate i Avg KDA**")
                        fig, ax = plt.subplots()
                        ax2 = ax.twinx()
                        ax.bar(top['champion'], top['winrate'])
                        ax2.plot(top['champion'], top['avg_kda'], marker='o')
                        ax.set_ylabel('Winrate (%)')
                        ax2.set_ylabel('Avg KDA')
                        st.pyplot(fig)

with col2:
    st.markdown("## Summoner quick tools")
    st.markdown("- Afegeix un summoner amb el script de utilitats\n- Executa manualment /trigger_extract i /trigger_process\n- Revisa MongoDB a través de Compass o mongosh")
