#!/usr/bin/env python3
"""Create noise tables, noise maps and comparison plots from ROOT files.

Usage:
    python3 create_analysis.py <root-file-or-directory> [skala=log]
                               [combine=True] [time_window_s=<seconds>]

Arguments:
    <root-file-or-directory>  A ROOT file, or a directory searched recursively
                              for CSM0 ROOT files and their CSM1 partners.
    skala=log                 Optional. Use a logarithmic y-axis for the
                              noise-rate plots. The default is linear.
    combine=True              For a directory, combine all measurements in
                              combined_noise_rates.png. With a single ROOT
                              file, the normal single-measurement plot is made.
    time_window_s=<seconds>   Optional fallback for old ROOT files without
                              event-window metadata or a matching hits CSV.

The ROOT files contain one tree per mezzanine, named card_tree_<index>. Each
tree provides hit channels for 24 tubes. 
"""

import argparse
import csv
import colorsys
from io import StringIO
import math
from pathlib import Path
import re
import subprocess


# Shared geometry for single-measurement and combined noise-rate plots.  The
# combined variant only adds overlaid data sets and a measurement-file legend.
RATE_PLOT_WIDTH = 3000
RATE_PLOT_ROW_HEIGHT = 950
RATE_LEFT_MARGIN = 0.08
RATE_RIGHT_MARGIN = 0.03
RATE_BOTTOM_MARGIN = 0.20
RATE_TOP_MARGIN = 0.10
RATE_MEZZ_LABEL_Y = 0.10
RATE_TUBE_LABEL_Y = 0.17
RATE_TITLE_Y = 0.93

# Hardware topology.  These values describe the fixed MiniDAQ/MDT layout, not
# an individual measurement.  The ROOT format stores only channels with hits,
# so the number of physically present (including quiet) channels cannot be
# reconstructed reliably from the hit data alone.
CHANNELS_PER_MEZZANINE = 24
MEZZANINES_PER_CSM = 20
MEZZANINES_PER_RATE_PANEL = 6

# Analysis categories.  These are deliberate classification limits rather
# than metadata supplied by a measurement.
NOISY_RATE_HZ = 1_000.0
HIGH_RATE_HZ = 10_000.0
VERY_HIGH_RATE_HZ = 100_000.0
RATE_AXIS_KHZ_THRESHOLD_HZ = 10_000.0

# Supported names if a ROOT producer stores the event window as a TParameter,
# TNamed/TObjString, or a scalar branch in a metadata tree.  Current files do
# not contain it, but newer files can therefore be handled without code edits.
EVENT_WINDOW_FIELDS = {
    "time_window_s": 1.0,
    "event_window_s": 1.0,
    "event_time_window_s": 1.0,
    "acquisition_window_s": 1.0,
    "time_window_us": 1e-6,
    "event_window_us": 1e-6,
    "event_time_window_us": 1e-6,
    "acquisition_window_us": 1e-6,
    "time_window_ns": 1e-9,
    "event_window_ns": 1e-9,
    "event_time_window_ns": 1e-9,
    "acquisition_window_ns": 1e-9,
}

MINI_DAQ_CONVERTER = Path(
    "/remote/ceph/user/k/kortner/software/BIS/for_QAQC/scripts/convert_mini_daq.py"
)


def create_root_files_if_needed(input_path: str):
    """Convert a directory only when it does not contain any ROOT files yet."""
    path = Path(input_path).expanduser()
    if not path.is_dir():
        return

    root_file_count = sum(
        candidate.is_file() and candidate.suffix.lower() == ".root"
        for candidate in path.rglob("*")
    )
    if root_file_count:
        print(
            f"Found {root_file_count} ROOT file(s) below {path}; "
            "continuing with the analysis."
        )
        print()
        return

    print(f"No ROOT files found below {path}.")
    if not MINI_DAQ_CONVERTER.parent.is_dir():
        print(f"Converter directory not found: {MINI_DAQ_CONVERTER.parent}")
        print("Continuing without ROOT-file conversion.")
        print()
        return

    print(f"Converter directory found: {MINI_DAQ_CONVERTER.parent}")
    print(f"Creating ROOT files with: {MINI_DAQ_CONVERTER}")
    subprocess.run(
        ["python3", str(MINI_DAQ_CONVERTER), input_path],
        check=True,
    )
    created_root_files = sum(
        candidate.is_file() and candidate.suffix.lower() == ".root"
        for candidate in path.rglob("*")
    )
    if not created_root_files:
        raise RuntimeError(
            "ROOT-file conversion completed, but no ROOT files were created "
            f"below {path}."
        )
    print(f"Created {created_root_files} ROOT file(s); continuing with the analysis.")
    print()


def run_stem_for_root(path: Path):
    """Return the measurement stem without a trailing CSM index."""
    return re.sub(r"_CSM[01]$", "", path.stem, flags=re.IGNORECASE)


def csm_name_for_root(path: Path):
    """Read the CSM index from the ROOT filename instead of guessing it."""
    match = re.search(r"_CSM([01])$", path.stem, re.IGNORECASE)
    if match is None:
        raise ValueError(
            f"ROOT filename must end in _CSM0.root or _CSM1.root: {path}"
        )
    return f"CSM{match.group(1)}"


def positive_float(value, description):
    """Convert and validate a positive, finite measurement value."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {description}: {value!r}") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"Invalid {description}: {value!r}")
    return number


def event_window_field_scale(name):
    """Return the seconds conversion factor for a supported metadata name."""
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return EVENT_WINDOW_FIELDS.get(normalized)


def resolve_root_files(input_path: str):
    """Return the requested ROOT file and its matching CSM partner."""
    path = Path(input_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.is_dir():
        raise ValueError(f"Expected a file, got a directory: {path}")
    if path.suffix.lower() != ".root":
        raise ValueError(f"Expected a .root file, got: {path}")

    csm_name = csm_name_for_root(path)
    selected = [path]
    other = "CSM1" if csm_name == "CSM0" else "CSM0"
    partner_name = f"{run_stem_for_root(path)}_{other}.root"
    partner = next(
        (
            candidate
            for candidate in path.parent.iterdir()
            if candidate.is_file()
            and candidate.suffix.lower() == ".root"
            and candidate.name.lower() == partner_name.lower()
        ),
        None,
    )
    if partner is not None:
        selected.append(partner)
    return selected


def resolve_root_file_groups(input_path: str):
    """Return one CSM0/CSM1 file group for each recursive measurement."""
    path = Path(input_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_dir():
        return [resolve_root_files(str(path))]

    csm0_files = sorted(
        candidate
        for candidate in path.rglob("*.root")
        if candidate.is_file() and re.search(r"_CSM0\.root$", candidate.name, re.IGNORECASE)
    )
    if not csm0_files:
        raise ValueError(f"No CSM0 .root files found in directory: {path}")
    return [resolve_root_files(str(csm0_file)) for csm0_file in csm0_files]


def configured_cards_for_file(path: Path, csm_name: str, available_cards):
    """Return configured local card indices when the run metadata is available."""
    run_stem = run_stem_for_root(path)
    configuration_path = path.with_name(f"{run_stem}_configuration.txt")
    if not configuration_path.exists():
        return set(available_cards)

    configuration = configuration_path.read_text(encoding="utf-8")
    selected_line = re.search(r"noise_scan_selected_mezz:\s*(.*)", configuration)
    if selected_line is None:
        return set(available_cards)

    chamber_id = 0 if csm_name == "CSM0" else 1
    selected_cards = re.findall(
        rf"\(\s*{chamber_id}\s*,\s*(\d+)\s*\)", selected_line.group(1)
    )
    return {int(card) for card in selected_cards}


def root_module():
    """Import ROOT with one consistent, user-facing error."""
    try:
        import ROOT
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ROOT is not available in the active Python environment. "
            "Run this script in the project environment that has ROOT installed."
        ) from exc
    return ROOT


def open_root_file(path: Path):
    """Open a ROOT file and return both the file handle and ROOT module."""
    ROOT = root_module()
    file_obj = ROOT.TFile.Open(str(path), "READ")
    if file_obj is None or file_obj.IsZombie():
        raise RuntimeError(f"Could not open ROOT file: {path}")
    return file_obj, ROOT


def scalar_from_root_object(obj):
    """Return a scalar value from common ROOT metadata object types."""
    if hasattr(obj, "GetVal"):
        return obj.GetVal()
    if obj.InheritsFrom("TObjString"):
        return str(obj.GetString())
    if obj.InheritsFrom("TNamed"):
        return obj.GetTitle()
    return None


def event_window_from_root_file(path: Path):
    """Read an explicitly stored event window from a ROOT metadata object."""
    file_obj, _ = open_root_file(path)
    try:
        directories = [file_obj]
        while directories:
            directory = directories.pop()
            for key in directory.GetListOfKeys():
                obj = key.ReadObj()
                if obj.InheritsFrom("TDirectory"):
                    directories.append(obj)
                    continue

                scale = event_window_field_scale(key.GetName())
                if scale is not None:
                    raw_value = scalar_from_root_object(obj)
                    if raw_value is not None:
                        value_s = positive_float(raw_value, key.GetName()) * scale
                        return value_s, f"ROOT metadata {key.GetName()}"

                if not obj.InheritsFrom("TTree") or obj.GetEntries() < 1:
                    continue
                for branch in obj.GetListOfBranches():
                    scale = event_window_field_scale(branch.GetName())
                    if scale is None:
                        continue
                    obj.GetEntry(0)
                    raw_value = getattr(obj, branch.GetName())
                    value_s = positive_float(raw_value, branch.GetName()) * scale
                    return value_s, f"ROOT branch {obj.GetName()}.{branch.GetName()}"
    finally:
        file_obj.Close()
    return None


def event_window_from_hits_csv(root_file: Path):
    """Read the per-event acquisition window from the matching hits CSV."""
    csv_path = root_file.with_name(f"{run_stem_for_root(root_file)}_hits.csv")
    if not csv_path.exists():
        return None

    values = set()
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None or "time_window_s" not in reader.fieldnames:
            return None
        values = {
            positive_float(row["time_window_s"], "time_window_s")
            for row in reader
            if row.get("time_window_s", "").strip()
        }

    if not values:
        return None
    if len(values) != 1:
        formatted = ", ".join(f"{value:g}" for value in sorted(values))
        raise ValueError(f"Inconsistent time_window_s values in {csv_path}: {formatted}")
    return values.pop(), csv_path.name


def event_window_from_configuration(root_file: Path):
    """Read an event-window field from a matching configuration, if present."""
    configuration_path = root_file.with_name(
        f"{run_stem_for_root(root_file)}_configuration.txt"
    )
    if not configuration_path.exists():
        return None

    configuration = configuration_path.read_text(encoding="utf-8")
    for field_name, scale in EVENT_WINDOW_FIELDS.items():
        match = re.search(
            rf"^{re.escape(field_name)}\s*[:=]\s*([^\s#]+)",
            configuration,
            re.MULTILINE | re.IGNORECASE,
        )
        if match:
            value_s = positive_float(match.group(1), field_name) * scale
            return value_s, configuration_path.name
    return None


def consistent_event_window(candidates):
    """Validate matching event-window values and return the first candidate."""
    if not candidates:
        return None
    reference_value, reference_source = candidates[0]
    for value, source in candidates[1:]:
        if not math.isclose(value, reference_value, rel_tol=1e-12, abs_tol=0.0):
            raise ValueError(
                "Conflicting event windows: "
                f"{reference_value:g} s ({reference_source}) and {value:g} s ({source})"
            )
    return reference_value, reference_source


def resolve_event_window(files, override_s=None):
    """Resolve one event window for a CSM file group using reliable metadata."""
    if override_s is not None:
        return positive_float(override_s, "time_window_s"), "command line"

    root_candidates = []
    for path in files:
        candidate = event_window_from_root_file(path)
        if candidate is not None:
            value, source = candidate
            root_candidates.append((value, f"{path.name}: {source}"))

    # Both CSM files of a measurement share these companion files.  Deduplicate
    # them so an inconsistency check compares independent sources only.
    unique_roots = {}
    for path in files:
        unique_roots.setdefault((path.parent, run_stem_for_root(path)), path)

    csv_candidates = []
    for path in unique_roots.values():
        candidate = event_window_from_hits_csv(path)
        if candidate is not None:
            csv_candidates.append(candidate)

    configuration_candidates = []
    for path in unique_roots.values():
        candidate = event_window_from_configuration(path)
        if candidate is not None:
            configuration_candidates.append(candidate)
    result = consistent_event_window(
        root_candidates + csv_candidates + configuration_candidates
    )
    if result is not None:
        return result

    raise ValueError(
        "The event window is not stored in the selected ROOT file(s), and no "
        "matching hits CSV/configuration contains it. Supply "
        "time_window_s=<seconds>; no measurement-independent value is assumed."
    )


def read_noise_rates_for_file(path: Path, event_window_s):
    """Read tube rates from all configured mezzanine trees in one ROOT file."""
    file_obj, ROOT = open_root_file(path)
    try:
        csm_name = csm_name_for_root(path)
        mezz_offset = 0 if csm_name == "CSM0" else MEZZANINES_PER_CSM
        card_indices = []
        for key in file_obj.GetListOfKeys():
            name = key.GetName()
            if name.startswith("card_tree_"):
                try:
                    card_indices.append(int(name.removeprefix("card_tree_")))
                except ValueError:
                    continue

        active_cards = configured_cards_for_file(path, csm_name, card_indices)
        results = {csm_name: {}}
        for card in sorted(active_cards):
            mezz_label = f"Mezz{mezz_offset + card:02d}"
            results[csm_name][mezz_label] = {
                tube: 0.0 for tube in range(CHANNELS_PER_MEZZANINE)
            }
            tree = file_obj.Get(f"card_tree_{card}")
            if tree is None or not bool(tree) or tree.ClassName() == "TObject":
                continue

            entries = max(1, int(tree.GetEntriesFast()))
            first_channel = card * CHANNELS_PER_MEZZANINE
            ROOT.gROOT.cd()
            histogram_name = f"channel_counts_{csm_name}_{card}"
            histogram = ROOT.TH1D(
                histogram_name,
                "",
                CHANNELS_PER_MEZZANINE,
                first_channel - 0.5,
                first_channel + CHANNELS_PER_MEZZANINE - 0.5,
            )
            histogram.SetDirectory(ROOT.gROOT)
            tree.Draw(f"hit_channel>>{histogram_name}", "", "goff")
            channel_counts = [
                int(histogram.GetBinContent(channel + 1))
                for channel in range(CHANNELS_PER_MEZZANINE)
            ]
            histogram.SetDirectory(0)

            for channel in range(CHANNELS_PER_MEZZANINE):
                count = channel_counts[channel]
                if count == 0:
                    continue
                rate_hz = count / (event_window_s * entries)

                results[csm_name][mezz_label][channel] = rate_hz

        return results
    finally:
        file_obj.Close()


def print_table(results, output=None):
    """Write the rate table for all CSMs and mezzanines to a text stream."""
    for csm_name in sorted(results, key=lambda name: name):
        if not results[csm_name]:
            continue
        print(f"{csm_name}", file=output)
        for mezz_label in sorted(results[csm_name], key=lambda x: int(x[4:])):
            print(f"  {mezz_label}:", file=output)
            tube_rates = results[csm_name][mezz_label]
            for tube_index in sorted(tube_rates):
                print(f"    Tube {tube_index}: {tube_rates[tube_index]:.2f} Hz", file=output)
        print(file=output)


def read_run_metadata(root_file: Path):
    """Read threshold, hysteresis and duration metadata beside a ROOT file."""
    run_stem = run_stem_for_root(root_file)
    configuration_path = root_file.with_name(f"{run_stem}_configuration.txt")
    summary_path = root_file.with_name(f"{run_stem}_summary.txt")
    metadata = {
        "threshold": "unknown",
        "hysteresis": "unknown",
        "duration_s": "unknown",
    }

    if configuration_path.exists():
        configuration = configuration_path.read_text(encoding="utf-8")
        for key, metadata_key in (
            ("noise_scan_threshold", "threshold"),
            ("noise_scan_hyst", "hysteresis"),
        ):
            match = re.search(rf"^{key}:\s*(.+)$", configuration, re.MULTILINE)
            if match:
                metadata[metadata_key] = match.group(1).strip()

    if summary_path.exists():
        summary = summary_path.read_text(encoding="utf-8")
        match = re.search(r"^duration_s:\s*(.+)$", summary, re.MULTILINE)
        if match:
            metadata["duration_s"] = f"{float(match.group(1)):.2f}"

    return metadata


def create_noise_map(results, root_file: Path):
    """Create the chamber overview map for one measurement."""
    ROOT = root_module()
    metadata = read_run_metadata(root_file)
    # The 5:1 canvas ratio together with these margins matches the final world
    # coordinate ratio exactly.  Rendering large gives ROOT enough pixels for
    # visibly smooth tube edges when viewers scale the PNG down.
    canvas = ROOT.TCanvas("noise_map", "Noise map", 6400, 1480)
    canvas.SetCanvasSize(6400, 1480)
    canvas.SetMargin(0.01, 0.01, 0.08, 0.08)
    colors = {
        "quiet": ROOT.kGray + 1,
        "yellow": ROOT.kYellow,
        "orange": ROOT.kOrange + 1,
        "red": ROOT.kRed,
    }

    def noise_color(rate_hz):
        if rate_hz > VERY_HIGH_RATE_HZ:
            return colors["red"]
        if rate_hz > HIGH_RATE_HZ:
            return colors["orange"]
        if rate_hz > NOISY_RATE_HZ:
            return colors["yellow"]
        return colors["quiet"]

    def tube_positions():
        positions = {}
        for column in range(CHANNELS_PER_MEZZANINE // 4):
            positions[4 * column] = (column, 1)
            positions[4 * column + 1] = (column, 0)
            positions[4 * column + 2] = (column, 3)
            positions[4 * column + 3] = (column, 2)
        return positions

    positions = tube_positions()
    csm_width = 54.0
    mezz_width = 5.7
    mezz_height = 4.0
    block_y = {"top": 6.0, "bottom": 0.0}
    csm_x = {"CSM0": 0.0, "CSM1": csm_width + 2.0}
    canvas.Range(-1.0, -4.2, csm_x["CSM1"] + csm_width + 1.0, 18.0)
    total_tubes = 0
    noisy_tubes = 0
    average_rates = []
    drawn_objects = []

    def draw_text(text, x, y, size, align=12, bold=False):
        label = ROOT.TLatex(x, y, text)
        label.SetTextSize(size)
        label.SetTextAlign(align)
        label.SetTextFont(62 if bold else 42)
        label.Draw()
        drawn_objects.append(label)

    def draw_circle(x, y, radius, fill_color, line_color, line_width=1):
        circle = ROOT.TEllipse(x, y, radius, radius)
        circle.SetFillColor(fill_color)
        circle.SetLineColor(line_color)
        circle.SetLineWidth(line_width)
        circle.Draw()
        drawn_objects.append(circle)

    for csm_name in ("CSM0", "CSM1"):
        draw_text(csm_name, csm_x[csm_name] + csm_width / 2, 11.45, 0.060, 22, True)
        for mezz_label, tube_rates in results.get(csm_name, {}).items():
            mezz_number = int(mezz_label[4:])
            local_number = (
                mezz_number
                if csm_name == "CSM0"
                else mezz_number - MEZZANINES_PER_CSM
            )
            pair_column = local_number // 2
            row_name = "top" if local_number % 2 else "bottom"
            x_origin = csm_x[csm_name] + pair_column * mezz_width
            y_origin = block_y[row_name]
            box = ROOT.TBox(x_origin, y_origin, x_origin + mezz_width - 0.25, y_origin + mezz_height)
            box.SetFillStyle(0)
            box.SetLineColor(ROOT.kGray + 1)
            box.Draw()
            drawn_objects.append(box)
            draw_text(mezz_label, x_origin + (mezz_width - 0.25) / 2, y_origin + mezz_height + 0.25, 0.028, 22, True)
            for tube_number, (column, row) in positions.items():
                rate_hz = tube_rates.get(tube_number, 0.0)
                x = x_origin + 0.82 + column * 0.82 - (0.41 if row in (1, 3) else 0.0)
                y = y_origin + 0.50 + (3 - row) * 0.82
                draw_circle(x, y, 0.36, noise_color(rate_hz), ROOT.kGray + 1, 2)
                total_tubes += 1
                average_rates.append(rate_hz)
                if rate_hz > NOISY_RATE_HZ:
                    noisy_tubes += 1

    average_rate = sum(average_rates) / len(average_rates) if average_rates else 0.0
    draw_text(f"File: {root_file.name}", 0, 17.0, 0.028, 12)
    draw_text(
        f"Threshold: {metadata['threshold']}    Hysteresis: {metadata['hysteresis']}    "
        f"Duration: {metadata['duration_s']} s",
        0, 16.0, 0.030, 12,
    )
    draw_text(
        f"Average noise rate: {average_rate:.2f} Hz    "
        f"Noisy tubes (>1 kHz): {noisy_tubes}/{total_tubes}",
        0, 14.8, 0.038, 12, True,
    )
    legend_x = 0.0
    for label, color in (("<= 1 kHz", colors["quiet"]), ("> 1 kHz", colors["yellow"]), ("> 10 kHz", colors["orange"]), ("> 100 kHz", colors["red"])):
        draw_circle(legend_x + 0.4, -1.9, 0.32, color, ROOT.kGray + 1, 2)
        draw_text(label, legend_x + 0.9, -2.05, 0.040, 12)
        legend_x += 7.5

    legend_x = csm_x["CSM1"] + csm_width - 5.0
    legend_y = -3.5
    draw_text("Tube numbering", legend_x, legend_y + 3.7, 0.034, 22, True)
    for tube_number, (column, row) in positions.items():
        x = legend_x - 2.05 + column * 0.82 - (0.41 if row in (1, 3) else 0.0)
        y = legend_y + (3 - row) * 0.82
        draw_circle(x, y, 0.36, ROOT.kWhite, ROOT.kGray + 2, 2)
        draw_text(str(tube_number), x, y - 0.055, 0.018, 22)

    output_path = root_file.with_name(f"{run_stem_for_root(root_file)}_noise_map.png")
    canvas.SaveAs(str(output_path))
    canvas.Close()
    return output_path


def rate_group_label(csm_name, mezz_labels):
    """Build a compact label for one mezzanine group."""
    if len(mezz_labels) == 1:
        mezz_text = mezz_labels[0]
    elif all(
        int(label[4:]) == int(mezz_labels[0][4:]) + index
        for index, label in enumerate(mezz_labels)
    ):
        mezz_text = f"{mezz_labels[0]} - {mezz_labels[-1]}"
    else:
        mezz_text = ", ".join(mezz_labels)
    return f"{csm_name}: {mezz_text}"


def rate_axis_unit(maximum_rate_hz):
    """Choose a readable y-axis unit from the automatic panel maximum."""
    maximum_plot_rate_hz = max(1.0, maximum_rate_hz * 1.15)
    if maximum_plot_rate_hz > RATE_AXIS_KHZ_THRESHOLD_HZ:
        return 1_000.0, "kHz"
    return 1.0, "Hz"


def style_rate_plot(ROOT, plot, maximum_rate_hz, axis_divisor, logarithmic):
    """Apply the shared axes and pad style to a rate graph or multigraph."""
    minimum_rate = (0.1 if logarithmic else 0.0) / axis_divisor
    maximum_rate = max(1.0, maximum_rate_hz * 1.15) / axis_divisor
    plot.SetMinimum(minimum_rate)
    plot.SetMaximum(maximum_rate)

    pad = ROOT.gPad
    pad.SetLeftMargin(RATE_LEFT_MARGIN)
    pad.SetRightMargin(RATE_RIGHT_MARGIN)
    pad.SetBottomMargin(RATE_BOTTOM_MARGIN)
    pad.SetTopMargin(RATE_TOP_MARGIN)
    pad.SetLogy(logarithmic)
    pad.SetGridy()

    panel_channels = MEZZANINES_PER_RATE_PANEL * CHANNELS_PER_MEZZANINE
    x_axis = plot.GetXaxis()
    x_axis.SetLimits(-2.0, panel_channels + 1.0)
    x_axis.SetLabelSize(0.0)
    x_axis.SetTickLength(0.0)
    y_axis = plot.GetYaxis()
    y_axis.SetLabelSize(0.032)
    y_axis.SetTitleSize(0.038)
    y_axis.SetTitleOffset(0.95)
    return minimum_rate, maximum_rate


def draw_rate_panel_labels(
    ROOT,
    csm_name,
    mezz_labels,
    minimum_rate,
    maximum_rate,
    axis_divisor,
    drawn_objects,
):
    """Draw the shared separators, tube labels, threshold and panel title."""
    panel_channels = MEZZANINES_PER_RATE_PANEL * CHANNELS_PER_MEZZANINE
    plot_width = 1.0 - RATE_LEFT_MARGIN - RATE_RIGHT_MARGIN

    def x_to_ndc(x_value):
        return RATE_LEFT_MARGIN + plot_width * (x_value + 2.0) / (
            panel_channels + 3.0
        )

    def vertical_line(x_value, color, width):
        line = ROOT.TLine(x_value, minimum_rate, x_value, maximum_rate)
        line.SetLineColor(color)
        line.SetLineWidth(width)
        line.Draw()
        drawn_objects.append(line)

    for mezz_index, mezz_label in enumerate(mezz_labels):
        x_start = mezz_index * CHANNELS_PER_MEZZANINE
        vertical_line(x_start - 0.5, ROOT.kGray + 2, 3)
        for tube_boundary in (5, 10, 15, 20):
            vertical_line(x_start + tube_boundary, ROOT.kGray + 1, 1)

        label = ROOT.TLatex(
            x_to_ndc(x_start + 11.5), RATE_MEZZ_LABEL_Y, mezz_label
        )
        label.SetTextAlign(23)
        label.SetNDC(True)
        label.SetTextSize(0.040)
        label.SetTextFont(42)
        label.Draw()
        drawn_objects.append(label)

        for tube_index in range(0, CHANNELS_PER_MEZZANINE, 5):
            tube_label = ROOT.TLatex(
                x_to_ndc(x_start + tube_index),
                RATE_TUBE_LABEL_Y,
                str(tube_index),
            )
            tube_label.SetTextAlign(23)
            tube_label.SetNDC(True)
            tube_label.SetTextSize(0.024)
            tube_label.Draw()
            drawn_objects.append(tube_label)

    vertical_line(
        len(mezz_labels) * CHANNELS_PER_MEZZANINE - 0.5,
        ROOT.kGray + 2,
        3,
    )
    threshold_rate = NOISY_RATE_HZ / axis_divisor
    threshold_line = ROOT.TLine(
        -2.0, threshold_rate, panel_channels + 1.0, threshold_rate
    )
    threshold_line.SetLineColor(ROOT.kBlack)
    threshold_line.SetLineWidth(4)
    threshold_line.Draw()
    drawn_objects.append(threshold_line)

    title = ROOT.TLatex(0.02, RATE_TITLE_Y, rate_group_label(csm_name, mezz_labels))
    title.SetNDC(True)
    title.SetTextSize(0.060)
    title.SetTextFont(62)
    title.Draw()
    drawn_objects.append(title)


def create_noise_rate_plot(results, root_file: Path, logarithmic=False):
    """Create the detailed rate plot for one measurement."""
    ROOT = root_module()
    mezzanine_groups = []
    for csm_name in ("CSM0", "CSM1"):
        mezzanines = sorted(
            results.get(csm_name, {}).items(),
            key=lambda item: int(item[0][4:]),
        )
        for group_start in range(0, len(mezzanines), MEZZANINES_PER_RATE_PANEL):
            mezzanine_groups.append(
                (
                    csm_name,
                    mezzanines[group_start:group_start + MEZZANINES_PER_RATE_PANEL],
                )
            )

    if not mezzanine_groups:
        return None

    rows = len(mezzanine_groups)
    canvas = ROOT.TCanvas(
        "noise_rates",
        "Noise rates by mezzanine",
        RATE_PLOT_WIDTH,
        rows * RATE_PLOT_ROW_HEIGHT,
    )
    canvas.Divide(1, rows, 0.0, 0.0)
    drawn_objects = []

    for pad_index, (csm_name, mezzanines) in enumerate(mezzanine_groups, start=1):
        canvas.cd(pad_index)
        points = []
        for mezz_index, (mezz_label, tube_rates) in enumerate(mezzanines):
            for tube_index in range(CHANNELS_PER_MEZZANINE):
                rate_hz = tube_rates.get(tube_index, 0.0)
                points.append(
                    (mezz_index * CHANNELS_PER_MEZZANINE + tube_index, rate_hz)
                )

        maximum_rate = max((rate_hz for _, rate_hz in points), default=0.0)
        axis_divisor, axis_unit = rate_axis_unit(maximum_rate)
        graph = ROOT.TGraph(len(points))
        for point_index, (x_value, rate_hz) in enumerate(points):
            graph.SetPoint(point_index, x_value, rate_hz / axis_divisor)
        mezz_labels = [mezz_label for mezz_label, _ in mezzanines]
        graph.SetTitle(f";;Noise rate [{axis_unit}]")
        graph.SetMarkerStyle(20)
        graph.SetMarkerSize(0.8)
        graph.SetLineColor(ROOT.kBlue + 1)
        graph.SetMarkerColor(ROOT.kBlue + 1)
        graph.Draw("ALP")
        drawn_objects.append(graph)
        minimum_rate, maximum_plot_rate = style_rate_plot(
            ROOT, graph, maximum_rate, axis_divisor, logarithmic
        )
        draw_rate_panel_labels(
            ROOT,
            csm_name,
            mezz_labels,
            minimum_rate,
            maximum_plot_rate,
            axis_divisor,
            drawn_objects,
        )

    output_path = root_file.with_name(
        f"{run_stem_for_root(root_file)}_noise_rates.png"
    )
    canvas.SaveAs(str(output_path))
    canvas.Close()
    return output_path


def create_combined_noise_rate_plot(measurements, output_path: Path, logarithmic=False):
    """Create one color-coded rate comparison plot for several measurements."""
    ROOT = root_module()
    mezzanine_groups = []
    for csm_name in ("CSM0", "CSM1"):
        mezz_labels = sorted(
            {
                mezz_label
                for measurement in measurements
                for mezz_label in measurement["results"].get(csm_name, {})
            },
            key=lambda label: int(label[4:]),
        )
        for group_start in range(0, len(mezz_labels), MEZZANINES_PER_RATE_PANEL):
            mezzanine_groups.append(
                (
                    csm_name,
                    mezz_labels[group_start:group_start + MEZZANINES_PER_RATE_PANEL],
                )
            )

    if not mezzanine_groups:
        return None

    rows = len(mezzanine_groups)
    plot_height = RATE_PLOT_ROW_HEIGHT
    legend_rows = len(measurements)
    # The legend contains one header row plus the actual file rows.  Size it to
    # that content instead of leaving a large, mostly empty box below the plots.
    legend_height = 100 + legend_rows * 80
    canvas_height = rows * plot_height + legend_height
    canvas = ROOT.TCanvas(
        "combined_noise_rates", "Combined noise rates", RATE_PLOT_WIDTH, canvas_height
    )
    colors = []
    for measurement_index in range(len(measurements)):
        hue = measurement_index / max(1, len(measurements))
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.80, 0.85)
        colors.append(
            ROOT.TColor.GetColor(
                f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"
            )
        )
    drawn_objects = []

    for pad_index, (csm_name, mezz_labels) in enumerate(mezzanine_groups):
        y_low = (legend_height + (rows - pad_index - 1) * plot_height) / canvas_height
        y_high = (legend_height + (rows - pad_index) * plot_height) / canvas_height
        plot_pad = ROOT.TPad(f"combined_plot_{pad_index}", "", 0.0, y_low, 1.0, y_high)
        canvas.cd()
        plot_pad.Draw()
        plot_pad.cd()
        drawn_objects.append(plot_pad)
        multigraph = ROOT.TMultiGraph()
        point_series = []
        for measurement_index, measurement in enumerate(measurements):
            points = []
            results = measurement["results"].get(csm_name, {})
            for mezz_index, mezz_label in enumerate(mezz_labels):
                tube_rates = results.get(mezz_label)
                if tube_rates is None:
                    continue
                for tube_index in range(CHANNELS_PER_MEZZANINE):
                    rate_hz = tube_rates.get(tube_index, 0.0)
                    points.append(
                        (
                            mezz_index * CHANNELS_PER_MEZZANINE + tube_index,
                            rate_hz,
                        )
                    )
            if points:
                point_series.append((measurement_index, points))

        maximum_rate = max(
            (rate_hz for _, points in point_series for _, rate_hz in points),
            default=0.0,
        )
        axis_divisor, axis_unit = rate_axis_unit(maximum_rate)
        for measurement_index, points in point_series:
            graph = ROOT.TGraph(len(points))
            for point_index, (x_value, rate_hz) in enumerate(points):
                graph.SetPoint(point_index, x_value, rate_hz / axis_divisor)
            color = colors[measurement_index % len(colors)]
            graph.SetLineColor(color)
            graph.SetMarkerColor(color)
            graph.SetMarkerStyle(20)
            graph.SetMarkerSize(0.8)
            multigraph.Add(graph, "LP")
            drawn_objects.append(graph)

        multigraph.SetTitle(f";;Noise rate [{axis_unit}]")
        multigraph.Draw("A")
        drawn_objects.append(multigraph)
        minimum_rate, maximum_plot_rate = style_rate_plot(
            ROOT, multigraph, maximum_rate, axis_divisor, logarithmic
        )
        draw_rate_panel_labels(
            ROOT,
            csm_name,
            mezz_labels,
            minimum_rate,
            maximum_plot_rate,
            axis_divisor,
            drawn_objects,
        )

    legend_pad = ROOT.TPad(
        "combined_legend", "", 0.0, 0.0, 1.0, legend_height / canvas_height
    )
    canvas.cd()
    legend_pad.Draw()
    legend_pad.cd()
    drawn_objects.append(legend_pad)
    legend = ROOT.TLegend(0.03, 0.06, 0.97, 0.94)
    legend.SetHeader("Measurement files", "C")
    legend.SetNColumns(1)
    # ROOT expresses the text size relative to the legend pad.  Scale it with
    # the number of rows so every filename keeps its own vertical space.
    legend.SetTextSize(min(0.20, 0.70 / (legend_rows + 1)))
    legend.SetMargin(0.08)
    for measurement_index, measurement in enumerate(measurements):
        legend_graph = ROOT.TGraph()
        color = colors[measurement_index % len(colors)]
        legend_graph.SetLineColor(color)
        legend_graph.SetMarkerColor(color)
        legend_graph.SetMarkerStyle(20)
        legend.AddEntry(legend_graph, measurement["label"], "LP")
        drawn_objects.append(legend_graph)
    legend.Draw()
    drawn_objects.append(legend)

    canvas.SaveAs(str(output_path))
    canvas.Close()
    return output_path


def process_root_group(
    files,
    logarithmic=False,
    create_rate_plot=True,
    event_window_override_s=None,
):
    """Create tables and maps for one CSM0/CSM1 measurement group."""
    print("Found files:")
    for p in files:
        print(f"  - {p}")
    print()

    event_window_s, event_window_source = resolve_event_window(
        files, event_window_override_s
    )
    print(
        f"Event window per event: {event_window_s:g} s "
        f"(source: {event_window_source})"
    )
    print()

    combined = {}
    for p in files:
        combined.update(read_noise_rates_for_file(p, event_window_s))

    table_buffer = StringIO()
    print_table(combined, table_buffer)
    table_text = table_buffer.getvalue()

    output_stem = run_stem_for_root(files[0])
    output_path = files[0].with_name(f"{output_stem}_noise_table.txt")
    output_path.write_text(table_text, encoding="utf-8")
    print(f"Table written to: {output_path}")
    high_buffer = StringIO()
    high_results = {
        csm_name: {
            mezz_label: {
                tube_index: rate_hz
                for tube_index, rate_hz in tube_map.items()
                if rate_hz > NOISY_RATE_HZ
            }
            for mezz_label, tube_map in mezz_map.items()
            if any(rate_hz > NOISY_RATE_HZ for rate_hz in tube_map.values())
        }
        for csm_name, mezz_map in combined.items()
    }
    print_table(high_results, high_buffer)
    high_table_path = files[0].with_name(f"{output_stem}_noise_table_high.txt")
    high_table_path.write_text(high_buffer.getvalue(), encoding="utf-8")
    print(f"High-noise table written to: {high_table_path}")
    map_path = create_noise_map(combined, files[0])
    print(f"Noise map written to: {map_path}")
    if create_rate_plot:
        noise_rates_path = create_noise_rate_plot(combined, files[0], logarithmic)
        print(f"Noise-rate plot written to: {noise_rates_path}")
    return combined


def main():
    """Parse command-line options and process each selected measurement."""
    parser = argparse.ArgumentParser(
        description="Create CSM/Mezz/Tube noise-rate tables and plots from ROOT files."
    )
    parser.add_argument(
        "root_file",
        help="Path to one ROOT file or a directory. Directories are searched recursively for *_CSM0.root files.",
    )
    parser.add_argument(
        "options",
        nargs="*",
        help=(
            "Optional arguments: skala=log for logarithmic y-axes; "
            "combine=True for one combined noise-rate plot; "
            "time_window_s=<seconds> as an explicit metadata fallback."
        ),
    )
    args = parser.parse_args()

    try:
        flag_options = set()
        event_window_override_s = None
        unknown_options = []
        for option in args.options:
            normalized = option.lower()
            if normalized in {"skala=log", "combine=true"}:
                flag_options.add(normalized)
                continue
            if normalized.startswith("time_window_s="):
                if event_window_override_s is not None:
                    raise ValueError("time_window_s was specified more than once")
                raw_value = option.split("=", 1)[1]
                event_window_override_s = positive_float(
                    raw_value, "time_window_s"
                )
                continue
            unknown_options.append(option)
        if unknown_options:
            raise ValueError(f"Unknown argument(s): {', '.join(sorted(unknown_options))}")
        logarithmic = "skala=log" in flag_options
        input_path = Path(args.root_file).expanduser()
        # Combining is a directory operation.  A directly selected ROOT file
        # keeps the original single-measurement behavior, even when the option
        # was supplied.
        combine = "combine=true" in flag_options and input_path.is_dir()
        create_root_files_if_needed(args.root_file)
        groups = resolve_root_file_groups(args.root_file)
        measurements = []
        for group_index, files in enumerate(groups):
            if group_index:
                print()
            results = process_root_group(
                files,
                logarithmic,
                not combine,
                event_window_override_s,
            )
            measurements.append({"results": results, "label": files[0].name})
        if combine:
            combined_output_path = input_path / "combined_noise_rates.png"
            combined_path = create_combined_noise_rate_plot(
                measurements, combined_output_path, logarithmic
            )
            print(f"Combined noise-rate plot written to: {combined_path}")
    except Exception as exc:
        print(f"Error: {exc}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
