import os
import uuid

class MultiXYPlotTool:
    schema = {
        "type": "function",
        "name": "generate_multi_xy_plot",
        "description": (
            "Generate a 2D plot with multiple datasets, each drawn as a line or dots. "
            "Each dataset must specify its type ('line' or 'dot'), label, and x/y as lists of objects with 'value' and 'label'. "
            "All datasets are drawn on the same plot, with a legend. "
            "Axis labels are combined from all datasets, so all values and labels are shown. "
            "Saves the generated image in the 'images' folder and returns the filename. "
            "Use this tool to visualize multiple series or collections of 2D data, with custom axis labels for each value."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "datasets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["line", "dot"], "description": "Type of plot for this dataset: 'line' or 'dot'."},
                            "label": {"type": "string", "description": "Label for this dataset (used in legend)."},
                            "x": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"value": {"type": "number"}, "label": {"type": "string"}},
                                    "required": ["value", "label"],
                                    "additionalProperties": False,
                                },
                                "description": "List of x objects: {value, label}.",
                            },
                            "y": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"value": {"type": "number"}, "label": {"type": "string"}},
                                    "required": ["value", "label"],
                                    "additionalProperties": False,
                                },
                                "description": "List of y objects: {value, label}.",
                            },
                        },
                        "required": ["type", "label", "x", "y"],
                        "additionalProperties": False,
                    },
                    "description": "List of datasets to plot. Each must specify type, label, x, y as lists of objects with value and label.",
                },
                "title": {"type": "string", "description": "Optional title for the plot."},
            },
            "required": ["datasets"],
            "additionalProperties": False,
        },
    }

    def __init__(self, images_folder="images"):
        self.images_folder = images_folder
        if not os.path.exists(images_folder):
            os.makedirs(images_folder)

    def run(self, datasets, title=None):
        try:
            import matplotlib.pyplot as plt
            fig = plt.figure()
            ax = fig.add_subplot(111)
            x_tick_map = {}
            y_tick_map = {}
            for dataset in datasets:
                for obj in dataset["x"]:
                    x_tick_map[obj["value"]] = obj["label"]
                for obj in dataset["y"]:
                    y_tick_map[obj["value"]] = obj["label"]
            x_ticks = sorted(x_tick_map.keys())
            x_labels = [x_tick_map[val] for val in x_ticks]
            y_ticks = sorted(y_tick_map.keys())
            y_labels = [y_tick_map[val] for val in y_ticks]
            for dataset in datasets:
                plot_type = dataset["type"]
                label = dataset["label"]
                x_vals = [obj["value"] for obj in dataset["x"]]
                y_vals = [obj["value"] for obj in dataset["y"]]
                if plot_type == "line":
                    ax.plot(x_vals, y_vals, label=label)
                elif plot_type == "dot":
                    ax.scatter(x_vals, y_vals, label=label)
            ax.set_xticks(x_ticks)
            ax.set_xticklabels(x_labels)
            ax.set_yticks(y_ticks)
            ax.set_yticklabels(y_labels)
            if title:
                ax.set_title(title)
            ax.set_xlabel('X')
            ax.set_ylabel('Y')
            ax.legend()
            filename = f"{self.images_folder}/multi_xy_plot_{uuid.uuid4().hex[:8]}.png"
            plt.tight_layout()
            plt.savefig(filename)
            plt.close(fig)
            return {"status": "success", "filename": filename}
        except Exception as e:
            return {"status": "error", "message": str(e)}
