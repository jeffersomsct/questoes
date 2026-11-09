#!/usr/bin/env python3
"""Lê taxonomia.txt e gera seed.sql (áreas, disciplinas, assuntos, relações). Rode: python3 gerar_seed.py"""
import itertools, collections
q = lambda s: "'" + s.replace("'", "''") + "'"
areas, discs, ass = [], [], []   # (id,cod,nome) / (id,area_id,cod,nome) / (id,disc_id,cod,nome)
seen = set()
for raw in open("taxonomia.txt", encoding="utf-8"):
    raw = raw.strip()
    if raw.startswith("# "):
        areas.append((len(areas)+1, f"AREA-{len(areas)+1:02d}", raw[2:]))
        n_disc = 0
    elif raw.startswith("## "):
        nome, itens = raw[3:].split(": ", 1)
        n_disc += 1
        did = len(discs)+1
        discs.append((did, areas[-1][0], f"DISC-{areas[-1][0]:02d}-{n_disc:02d}", nome))
        for i, it in enumerate(x.strip() for x in itens.split(";")):
            k = (did, it.lower()); assert k not in seen, f"duplicado: {nome} / {it}"; seen.add(k)
            ass.append((len(ass)+1, did, f"ASS-{areas[-1][0]:02d}-{n_disc:02d}-{i+1:03d}", it))

# Relações automáticas: mesmo nome em disciplinas diferentes (ex.: Orçamento público, Licenciamento ambiental)
por_nome = collections.defaultdict(list)
for a in ass: por_nome[a[3].lower()].append(a)
rel = {}
for lst in por_nome.values():
    for x, y in itertools.combinations(lst, 2):
        rel[(min(x[0], y[0]), max(x[0], y[0]))] = "MESMO_TEMA"
# Relações manuais (temas correlatos, nomes diferentes)
idx = {(next(d[3] for d in discs if d[0] == a[1]), a[3]): a[0] for a in ass}
manuais = [("Direito Administrativo", "Licitações", "Administração Pública", "Compras públicas"),
           ("Administração Pública", "Compras públicas", "Contabilidade Pública", "Execução orçamentária"),
           ("Direito Administrativo", "Licitações", "Contabilidade Pública", "Execução orçamentária"),
           ("Direito Penal", "Crimes em licitações", "Direito Administrativo", "Licitações"),
           ("Direito Ambiental", "Recursos hídricos", "Engenharia Ambiental", "Recursos hídricos")]
for d1, a1, d2, a2 in manuais:
    i, j = idx[(d1, a1)], idx[(d2, a2)]
    rel.setdefault((min(i, j), max(i, j)), "CORRELATO")

o = ["-- Gerado por gerar_seed.py a partir de taxonomia.txt. Rode DEPOIS de schema.sql.", "BEGIN;"]
o.append("INSERT INTO area (id,codigo,nome) VALUES\n" + ",\n".join(f"({i},{q(c)},{q(n)})" for i, c, n in areas) + ";")
o.append("INSERT INTO disciplina (id,area_id,codigo,nome) VALUES\n" + ",\n".join(f"({i},{a},{q(c)},{q(n)})" for i, a, c, n in discs) + ";")
o.append("INSERT INTO assunto (id,disciplina_id,codigo,nome) VALUES\n" + ",\n".join(f"({i},{d},{q(c)},{q(n)})" for i, d, c, n in ass) + ";")
o.append("INSERT INTO assunto_relacionado (assunto_a,assunto_b,tipo) VALUES\n" + ",\n".join(f"({a},{b},{q(t)})" for (a, b), t in sorted(rel.items())) + ";")
for t in ("area", "disciplina", "assunto"):
    o.append(f"SELECT setval(pg_get_serial_sequence('{t}','id'), (SELECT max(id) FROM {t}));")
o.append("COMMIT;")
open("seed.sql", "w", encoding="utf-8").write("\n".join(o) + "\n")
print(f"{len(areas)} áreas, {len(discs)} disciplinas, {len(ass)} assuntos, {len(rel)} relações")
