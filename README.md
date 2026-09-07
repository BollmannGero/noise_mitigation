# Auswertung der Noise-Messungen

Die komplette Auswertung wird mit `create_analysis.py` durchgeführt. Als
Argument kann entweder eine einzelne ROOT-Datei oder ein Ordner angegeben
werden:

```bash
python3 create_analysis.py /pfad/zur/messung
```

## Ablauf

Bei einem Ordner durchsucht das Skript zuerst den Ordner und alle Unterordner
nach ROOT-Dateien. Sind noch keine vorhanden, prüft es, ob folgender
Konverterordner verfügbar ist:

```text
/remote/ceph/user/k/kortner/software/BIS/for_QAQC/scripts
```

Dieser Pfad ist nur auf dem MPP-Server erreichbar. Wenn der
Ordner existiert, wird die Konvertierung mit folgendem Programm gestartet:

```text
/remote/ceph/user/k/kortner/software/BIS/for_QAQC/scripts/convert_mini_daq.py
```

Dabei wird intern derselbe Ordner übergeben, der beim Aufruf von
`create_analysis.py` angegeben wurde. Das entspricht diesem Befehl:

```bash
python3 /remote/ceph/user/k/kortner/software/BIS/for_QAQC/scripts/convert_mini_daq.py /pfad/zur/messung
```

Die Analyse beginnt erst, nachdem die Konvertierung beendet und mindestens
eine ROOT-Datei erstellt wurde. Ist der Konverterordner nicht vorhanden, wird
keine Konvertierung ausgeführt. Bereits vorhandene ROOT-Dateien werden nicht
neu erstellt oder überschrieben.

Danach sucht das Skript nach zusammengehörigen CSM0/CSM1-Dateien. Zu jeder
Datei mit der Endung `_CSM0.root` wird im gleichen Ordner die passende
`_CSM1.root` gesucht. Wird direkt eine einzelne ROOT-Datei angegeben, wird
entsprechend nach ihrem CSM-Partner gesucht. Ein fehlender Partner verhindert
die Auswertung der vorhandenen Datei nicht.

Für jedes gefundene Messpaar werden die Treffer pro Tube aus den ROOT-Trees
gelesen und mithilfe des Event-Zeitfensters in Noise-Raten umgerechnet. Das
Zeitfenster wird aus ROOT-Metadaten, einer passenden `_hits.csv` oder einer
`_configuration.txt` gelesen. Falls es dort nicht gespeichert ist, muss es
mit `time_window_s=<sekunden>` angegeben werden.

## Erzeugte Dateien

Die Ergebnisse werden neben den jeweiligen ROOT-Dateien gespeichert:

- `<messung>_noise_table.txt`: alle gemessenen Noise-Raten in Hz, sortiert
  nach CSM, Mezzanine und Tube.
- `<messung>_noise_table_high.txt`: nur Tubes mit einer Noise-Rate über
  1 kHz.
- `<messung>_noise_map.png`: grafische Übersicht der Tubes. Die Farben
  unterscheiden Raten bis 1 kHz, über 1 kHz, über 10 kHz und über 100 kHz.
- `<messung>_noise_rates.png`: Noise-Rate-Plot für eine einzelne Messung.
- `<messung>_noise_rates_layers.png`: acht Noise-Rate-Plots, angeordnet nach
  den vier physischen Tube-Layern. Zuerst werden die ungeraden Mezzanines und
  danach die geraden Mezzanines dargestellt. Die Layer enthalten nacheinander
  die Tubes `1, 5, 9, 13, 17, 21`, `0, 4, 8, 12, 16, 20`,
  `3, 7, 11, 15, 19, 23` und `2, 6, 10, 14, 18, 22`. Die x-Achse zeigt nur
  die jeweilige Mezzanine-Nummer. Bei nicht verwendeten Mezzanines wird die
  Verbindungslinie unterbrochen. Am unteren Bildrand ist zusätzlich die
  physische Tube-Nummerierung eingezeichnet.

Die gefundenen Eingabedateien, das verwendete Event-Zeitfenster und die Pfade
der erzeugten Dateien werden im Terminal ausgegeben. Die vollständigen
Noise-Raten stehen nur in den Texttabellen und werden nicht im Terminal
aufgelistet.

## Optionale Argumente

- `skala=log` verwendet eine logarithmische y-Achse für die Noise-Rate-Plots.
- `combine=True` erzeugt bei einem Ordner einen gemeinsamen Vergleichsplot
  `combined_noise_rates.png` und den nach Tube-Layern angeordneten Plot
  `combined_noise_rates_layers.png` im angegebenen Ordner. Die einzelnen
  `_noise_rates.png`- und `_noise_rates_layers.png`-Plots werden in diesem Fall
  nicht erzeugt.
- `time_window_s=<sekunden>` gibt das Event-Zeitfenster ausdrücklich vor und
  wird verwendet, wenn es nicht zuverlässig aus den Messdateien gelesen
  werden kann.

Beispiel mit mehreren Optionen:

```bash
python3 create_analysis.py /pfad/zur/messung combine=True skala=log time_window_s=0.0002
```
