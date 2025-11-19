from pymongo import MongoClient

# Connectem a la DB
client = MongoClient("mongodb://localhost:27017")
db = client["riot"]


def clean_duplicates():
    print("🧹 Iniciant neteja de duplicats...")

    # 1. Obtenim tots els jugadors
    summoners = list(db.summoners.find({}))
    seen_puuids = set()
    duplicates_count = 0

    for summ in summoners:
        puuid = summ.get("puuid")
        name = summ.get("summonerName")
        doc_id = summ.get("_id")

        if puuid in seen_puuids:
            # És un duplicat! L'esborrem
            print(f"🗑️ Esborrant duplicat: {name} ({doc_id})")
            db.summoners.delete_one({"_id": doc_id})
            duplicates_count += 1
        else:
            # És el primer cop que el veiem, el guardem com a 'vist'
            seen_puuids.add(puuid)

    print(f"✅ Neteja completada. S'han eliminat {duplicates_count} duplicats.")


if __name__ == "__main__":
    clean_duplicates()