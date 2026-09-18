import sys, os, sqlite3
print("питон:", sys.version.split()[0], "|", sys.executable)
con = sqlite3.connect("file:C:/seostat/data/seo.db?mode=ro", uri=True)
print("визитов в базе:", con.execute("SELECT COUNT(*) FROM visit").fetchone()[0])
print("максимальная дата:", con.execute("SELECT MAX(date) FROM visit").fetchone()[0])
con.close()
