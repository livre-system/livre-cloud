"""
Script para cargar las calles de Bahía Blanca en la DB con embeddings.
Ejecutar una sola vez: python seed_calles.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openai import OpenAI

CALLES_BAHIA_BLANCA = [
    "Alem", "Alsina", "Alvarado", "Alvear", "Antártida Argentina",
    "Arenales", "Avenida Alem", "Avenida Colón", "Avenida Fortín Mercedes",
    "Avenida Hipólito Yrigoyen", "Avenida Independencia", "Avenida Olimpo",
    "Avenida Pedro Aldini", "Avenida Pringles", "Avenida Rawson",
    "Avenida Rosario", "Avenida Sarmiento", "Avenida Stürzenbaum",
    "Belgrano", "Berutti", "Brown", "Bulnes",
    "Caseros", "Chiclana", "Colón", "Córdoba", "Corrientes",
    "Darragueira", "Donado", "Drago",
    "Echeverría", "Estomba",
    "Fitz Roy", "Fournier", "Fuerte Argentino",
    "Galería", "Garibaldi", "General Arias", "General Daniel Cerri",
    "General Paz", "General Pinto", "General Roca", "General San Martín",
    "Guido",
    "Haití", "Hipólito Yrigoyen",
    "Independencia", "Italia",
    "Juan B. Justo", "Junín",
    "Lainez", "Lamadrid", "Las Heras", "Lavalle", "Lebensohn",
    "Libertad", "Luro",
    "Matienzo", "Mendoza", "Mitre", "Moreno", "Moreno",
    "Newton",
    "O'Higgins", "Obligado", "Olascoaga", "Olimpo",
    "Patagonia", "Paunero", "Perú", "Pringles",
    "Rawson", "Rivadavia", "Rodriguez", "Rondeau",
    "San Lorenzo", "San Martín", "Sarmiento", "Soler", "Somellera",
    "Teniente Origone", "Tres Arroyos",
    "Undiano", "Urquiza",
    "Vieytes", "Viamonte",
    "Washington", "White",
    "Zelarrayán",
]

def seed():
    api_key = os.getenv("OPENAI_API_KEY", "")
    database_url = os.getenv("DATABASE_URL", "")

    if not api_key:
        print("ERROR: OPENAI_API_KEY no definida")
        return
    if not database_url:
        print("ERROR: DATABASE_URL no definida")
        return

    import psycopg2
    import psycopg2.extras
    client = OpenAI(api_key=api_key)

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Crear tabla si no existe
    cur.execute("""
        CREATE TABLE IF NOT EXISTS street_embeddings (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            embedding vector(1536),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    print("Tabla street_embeddings lista.")

    for calle in CALLES_BAHIA_BLANCA:
        # Saltar si ya existe
        cur.execute("SELECT 1 FROM street_embeddings WHERE name = %s", (calle,))
        if cur.fetchone():
            print(f"  Ya existe: {calle}")
            continue

        try:
            res = client.embeddings.create(model="text-embedding-3-small", input=calle)
            emb = res.data[0].embedding
            cur.execute(
                "INSERT INTO street_embeddings (name, embedding) VALUES (%s, %s::vector)",
                (calle, str(emb))
            )
            print(f"  OK: {calle}")
        except Exception as e:
            print(f"  ERROR {calle}: {e}")

    cur.close()
    conn.close()
    print("\nListo.")

if __name__ == "__main__":
    seed()
