"""Banco de questões — FastAPI + PostgreSQL.  Rodar: uvicorn main:app --reload"""
import os
from datetime import date
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/questoes")
app = FastAPI(title="Banco de Questões")
db = lambda: psycopg.connect(DSN, row_factory=dict_row)  # `with db()` faz commit ao sair


def run(sql, p=(), one=False):
    with db() as c:
        cur = c.execute(sql, p)
        if cur.description is None:
            return None
        return cur.fetchone() if one else cur.fetchall()


@app.on_event("startup")
def start():  # usuário único (id 1) até existir login
    run("INSERT INTO usuario(id,nome,email) VALUES(1,'Eu','eu@local') ON CONFLICT DO NOTHING")


def where(f):
    w, p = ["TRUE"], []
    for k, col in dict(disciplina_id="a.disciplina_id", banca_id="q.banca_id", concurso_id="q.concurso_id",
                       cargo_id="q.cargo_id", ano="q.ano").items():
        if f.get(k):
            w.append(f"{col}=%s"); p.append(int(f[k]))
    if f.get("assunto_id"):  # inclui subassuntos
        w.append("q.assunto_id IN (WITH RECURSIVE s AS (SELECT id FROM assunto WHERE id=%s UNION ALL "
                 "SELECT x.id FROM assunto x JOIN s ON x.parent_id=s.id) SELECT id FROM s)")
        p.append(int(f["assunto_id"]))
    return " AND ".join(w), p


BASE = """SELECT q.id,q.enunciado,q.tipo,q.ano,b.nome banca,c.nome concurso,cg.nome cargo,a.nome assunto,d.nome disciplina,
 (SELECT json_agg(json_build_object('letra',letra,'texto',texto) ORDER BY letra) FROM alternativa WHERE questao_id=q.id) alternativas,
 n.texto anotacao,COALESCE(v.vezes_respondida,0) vezes,COALESCE(v.erros,0) erros,v.tempo_medio_s tempo
 FROM questao q JOIN assunto a ON a.id=q.assunto_id JOIN disciplina d ON d.id=a.disciplina_id
 LEFT JOIN banca b ON b.id=q.banca_id LEFT JOIN concurso c ON c.id=q.concurso_id LEFT JOIN cargo cg ON cg.id=q.cargo_id
 LEFT JOIN anotacao n ON n.questao_id=q.id AND n.usuario_id=1
 LEFT JOIN v_desempenho_questao v ON v.questao_id=q.id AND v.usuario_id=1 WHERE """
ORD = {"aleatoria": "random()", "recentes": "q.id DESC", "erradas": "COALESCE(v.erros,0) DESC",
       "demoradas": "COALESCE(v.tempo_medio_s,0) DESC", "menos": "COALESCE(v.vezes_respondida,0) ASC"}


@app.get("/api/meta")
def meta():
    return {k: run(s) for k, s in dict(
        areas="SELECT id,nome FROM area ORDER BY id",
        disciplinas="SELECT id,area_id,nome FROM disciplina ORDER BY nome",
        assuntos="SELECT id,disciplina_id,parent_id,nome FROM assunto ORDER BY codigo",
        bancas="SELECT id,nome FROM banca ORDER BY nome",
        concursos="SELECT id,nome||' '||ano nome FROM concurso ORDER BY ano DESC",
        cargos="SELECT cg.id,cg.nome||' — '||c.nome nome FROM cargo cg JOIN concurso c ON c.id=cg.concurso_id ORDER BY 2",
        anos="SELECT DISTINCT ano id,ano nome FROM questao WHERE ano IS NOT NULL ORDER BY 1 DESC").items()}


@app.get("/api/questoes")
def listar(request: Request):
    f = dict(request.query_params)
    w, p = where(f)
    return run(BASE + w + f" ORDER BY {ORD.get(f.get('ordem'), 'random()')} LIMIT %s", p + [int(f.get("limite", 30))])


class QIn(BaseModel):
    assunto_id: int
    subassunto: str | None = None
    banca: str | None = None
    concurso: str | None = None
    cargo: str | None = None
    ano: int | None = None
    tipo: str = "MULTIPLA"
    enunciado: str
    gabarito: str
    comentario: str | None = None
    alternativas: dict[str, str] = {}


@app.post("/api/questoes")
def criar(q: QIn):
    with db() as c:
        one = lambda s, p: c.execute(s, p).fetchone()
        aid = q.assunto_id
        if q.subassunto:  # cria o subassunto sob demanda
            r = one("SELECT id FROM assunto WHERE parent_id=%s AND lower(nome)=lower(%s)", (aid, q.subassunto))
            if r:
                aid = r["id"]
            else:
                pa = one("SELECT * FROM assunto WHERE id=%s", (aid,))
                n = one("SELECT count(*)+1 n FROM assunto WHERE parent_id=%s", (aid,))["n"]
                aid = one("INSERT INTO assunto(disciplina_id,parent_id,codigo,nome,nivel) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                          (pa["disciplina_id"], aid, f"{pa['codigo']}.{n:02d}", q.subassunto, pa["nivel"] + 1))["id"]
        bid = cid = gid = None
        if q.banca:
            bid = one("INSERT INTO banca(nome) VALUES(%s) ON CONFLICT(nome) DO UPDATE SET nome=EXCLUDED.nome RETURNING id", (q.banca,))["id"]
        if q.concurso:
            cid = one("INSERT INTO concurso(nome,ano,banca_id) VALUES(%s,%s,%s) ON CONFLICT(nome,ano) DO UPDATE SET banca_id=EXCLUDED.banca_id RETURNING id",
                      (q.concurso, q.ano or date.today().year, bid))["id"]
            if q.cargo:
                gid = one("INSERT INTO cargo(concurso_id,nome) VALUES(%s,%s) ON CONFLICT(concurso_id,nome) DO UPDATE SET nome=EXCLUDED.nome RETURNING id",
                          (cid, q.cargo))["id"]
        qid = one("INSERT INTO questao(assunto_id,banca_id,concurso_id,cargo_id,ano,tipo,enunciado,gabarito,comentario) "
                  "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (aid, bid, cid, gid, q.ano, q.tipo, q.enunciado, q.gabarito, q.comentario))["id"]
        alts = {"C": "Certo", "E": "Errado"} if q.tipo == "CERTO_ERRADO" else {k: v for k, v in q.alternativas.items() if v}
        for l, t in alts.items():
            c.execute("INSERT INTO alternativa VALUES(%s,%s,%s)", (qid, l, t))
    return {"id": qid}


class R(BaseModel):
    questao_id: int
    resposta: str
    tempo: int = 0


@app.post("/api/responder")
def responder(r: R):
    q = run("SELECT gabarito,comentario FROM questao WHERE id=%s", (r.questao_id,), True)
    ok = r.resposta == q["gabarito"]
    run("INSERT INTO tentativa(usuario_id,questao_id,resposta,acertou,tempo_segundos) VALUES(1,%s,%s,%s,%s)",
        (r.questao_id, r.resposta, ok, r.tempo))
    return {"acertou": ok, **q}


class N(BaseModel):
    questao_id: int
    texto: str


@app.put("/api/anotacao")
def anotar(n: N):
    run("INSERT INTO anotacao(usuario_id,questao_id,texto) VALUES(1,%s,%s) ON CONFLICT(usuario_id,questao_id) "
        "DO UPDATE SET texto=EXCLUDED.texto,atualizada_em=now()", (n.questao_id, n.texto))
    return {"ok": True}


class S(BaseModel):
    titulo: str = "Prova"
    n: int = 20
    filtros: dict = {}


@app.post("/api/simulados")  # modo prova: gabarito não é enviado ao navegador
def criar_sim(s: S):
    w, p = where(s.filtros)
    qs = run(BASE + w + " ORDER BY random() LIMIT %s", p + [s.n])
    with db() as c:
        sid = c.execute("INSERT INTO simulado(usuario_id,titulo) VALUES(1,%s) RETURNING id", (s.titulo,)).fetchone()["id"]
        for o, q in enumerate(qs):
            c.execute("INSERT INTO simulado_questao VALUES(%s,%s,%s)", (sid, q["id"], o))
    return {"id": sid, "questoes": qs}


class F(BaseModel):
    respostas: dict[str, str]
    tempo: int


@app.post("/api/simulados/{sid}/finalizar")
def finalizar(sid: int, f: F):
    qs = run("SELECT q.id,q.gabarito,q.comentario FROM simulado_questao s JOIN questao q ON q.id=s.questao_id "
             "WHERE s.simulado_id=%s ORDER BY s.ordem", (sid,))
    por_questao = max(1, f.tempo // max(1, len(qs)))  # tempo total dividido igualmente
    with db() as c:
        for q in qs:
            r = f.respostas.get(str(q["id"]))
            q["resposta"], q["acertou"] = r, r == q["gabarito"]
            if r:
                c.execute("INSERT INTO tentativa(usuario_id,questao_id,simulado_id,resposta,acertou,tempo_segundos) VALUES(1,%s,%s,%s,%s,%s)",
                          (q["id"], sid, r, q["acertou"], por_questao))
        c.execute("UPDATE simulado SET finalizado_em=now() WHERE id=%s", (sid,))
    return qs


DIM = dict(area="ar.nome", disciplina="d.nome", assunto="a.nome", banca="b.nome", ano="q.ano::text",
           dia="t.respondida_em::date::text")


@app.get("/api/stats")  # ?por=area|disciplina|assunto|banca|ano|dia  (+ mesmos filtros)
def stats(request: Request):
    f = dict(request.query_params)
    por = f.get("por") if f.get("por") in DIM else "disciplina"
    w, p = where(f)
    return run(f"""SELECT COALESCE({DIM[por]},'(sem)') nome,count(*) respostas,count(*) FILTER (WHERE t.acertou) acertos,
      round(100.0*count(*) FILTER (WHERE t.acertou)/count(*),1) pct,round(avg(t.tempo_segundos)) tempo
      FROM tentativa t JOIN questao q ON q.id=t.questao_id JOIN assunto a ON a.id=q.assunto_id
      JOIN disciplina d ON d.id=a.disciplina_id JOIN area ar ON ar.id=d.area_id LEFT JOIN banca b ON b.id=q.banca_id
      WHERE t.usuario_id=1 AND {w} GROUP BY 1 ORDER BY {'1' if por in ('dia', 'ano') else 'pct'}""", p)

BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"

app.mount(
    "/",
    StaticFiles(directory=FRONTEND_DIR, html=True),
    name="frontend"
)