"""Visualization tools for charts and plots."""

import os
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..storage import get_app_data_dir


class MultiXYPlotTool:
    """Tool to generate multi-dataset XY plots."""
    
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir) if output_dir else (get_app_data_dir() / "images")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.schema = {
            "type": "function",
            "name": "generate_multi_xy_plot",
            "description": (
                "Generate a 2D plot with multiple datasets (line or dot). "
                "Saves the image and returns the filename."
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
                                "type": {
                                    "type": "string",
                                    "enum": ["line", "dot"],
                                    "description": "Plot type.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Dataset label.",
                                },
                                "x": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "value": {"type": "number"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["value", "label"],
                                        "additionalProperties": False,
                                    },
                                    "description": "X values with labels.",
                                },
                                "y": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "value": {"type": "number"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["value", "label"],
                                        "additionalProperties": False,
                                    },
                                    "description": "Y values with labels.",
                                },
                            },
                            "required": ["type", "label", "x", "y"],
                            "additionalProperties": False,
                        },
                        "description": "List of datasets to plot.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional plot title.",
                    },
                },
                "required": ["datasets"],
                "additionalProperties": False,
            },
        }
    
    def run(self, datasets: List[Dict], title: Optional[str] = None) -> Dict[str, Any]:
        try:
            import matplotlib.pyplot as plt
            
            fig, ax = plt.subplots()
            
            # Collect tick mappings
            x_ticks = {}
            y_ticks = {}
            
            for dataset in datasets:
                for obj in dataset["x"]:
                    x_ticks[obj["value"]] = obj["label"]
                for obj in dataset["y"]:
                    y_ticks[obj["value"]] = obj["label"]
            
            # Plot datasets
            for dataset in datasets:
                x_vals = [obj["value"] for obj in dataset["x"]]
                y_vals = [obj["value"] for obj in dataset["y"]]
                label = dataset["label"]
                
                if dataset["type"] == "line":
                    ax.plot(x_vals, y_vals, label=label)
                else:
                    ax.scatter(x_vals, y_vals, label=label)
            
            # Set ticks
            if x_ticks:
                x_sorted = sorted(x_ticks.keys())
                ax.set_xticks(x_sorted)
                ax.set_xticklabels([x_ticks[v] for v in x_sorted])
            
            if y_ticks:
                y_sorted = sorted(y_ticks.keys())
                ax.set_yticks(y_sorted)
                ax.set_yticklabels([y_ticks[v] for v in y_sorted])
            
            if title:
                ax.set_title(title)
            
            ax.set_xlabel("X")
            ax.set_ylabel("Y")
            ax.legend()
            
            filename = self.output_dir / f"plot_{uuid.uuid4().hex[:8]}.png"
            plt.tight_layout()
            plt.savefig(filename)
            plt.close(fig)
            
            return {"status": "success", "filename": str(filename)}
        except ImportError:
            return {"status": "error", "message": "matplotlib not installed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
