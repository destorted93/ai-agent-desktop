class WebSearchTool:
    def __init__(self):
        self.schema = {"type": "web_search_preview"}

    def run(self, query):
        return {"status": "success"}

class ImageGenerationTool:
    def __init__(self, quality="medium"):
        self.schema = {
            "type": "image_generation",
            "background": 'auto',
            "model": "gpt-image-1",
            "output_format": "png",
            "partial_images": 3,
            "quality": quality,
            "size": "auto",
        }

    def run(self, query):
        return {"status": "success"}
