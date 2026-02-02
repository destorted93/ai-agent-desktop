"""Vector database management with ChromaDB for RAG system."""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Union

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    chromadb = None  # type: ignore

try:
    from langchain_community.document_loaders import TextLoader, Docx2txtLoader, PyPDFLoader
except ImportError:
    TextLoader = None  # type: ignore
    Docx2txtLoader = None  # type: ignore
    PyPDFLoader = None  # type: ignore

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    try:
        from langchain.text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        RecursiveCharacterTextSplitter = None  # type: ignore

from .secure import get_app_data_dir


class VectorDBManager:
    """Manages ChromaDB vector database for RAG system.
    
    Provides interfaces for:
    - Collection management (create, read, update, delete)
    - Document chunking and embedding
    - Querying collections (single or multiple)
    - Metadata tracking for GUI display
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small"
    ):
        """Initialize the vector database manager.
        
        Args:
            api_key: OpenAI API key for embeddings
            base_url: Custom API base URL
            embedding_model: OpenAI embedding model name
        """
        if not chromadb:
            raise RuntimeError("chromadb package not available. Install with: pip install chromadb")
        
        self.api_key = api_key
        self.base_url = base_url
        self.embedding_model = embedding_model
        
        # Initialize OpenAI client for embeddings
        self.openai_client: Optional[Any] = None
        if api_key:
            self._init_client()
        
        # Initialize OpenAI client for embeddings
        self.openai_client: Optional[Any] = None
        if api_key:
            self._init_client()
        
        # Initialize ChromaDB
        self.db_path = get_app_data_dir() / "ai-agent-desktop-chromadb"
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True
            )
        )
    
    def _init_client(self) -> None:
        """Initialize the OpenAI client for embeddings."""
        if not self.api_key:
            self.openai_client = None
            return
        
        try:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self.openai_client = OpenAI(**kwargs)
        except Exception as e:
            print(f"Failed to initialize OpenAI client for embeddings: {e}")
            self.openai_client = None
    
    def update_credentials(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Update API credentials and reinitialize client.
        
        Args:
            api_key: New OpenAI API key
            base_url: New API base URL
        """
        if api_key is not None:
            self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        self._init_client()
    
    def _create_embedding(self, text: str) -> List[float]:
        """Create embedding for text using OpenAI API.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        if not self.openai_client:
            raise RuntimeError("OpenAI client not initialized. Cannot create embeddings.")
        
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"Failed to create embedding: {str(e)}")
    
    def _create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings for multiple texts in batch.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        if not self.openai_client:
            raise RuntimeError("OpenAI client not initialized. Cannot create embeddings.")
        
        try:
            response = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise RuntimeError(f"Failed to create embeddings: {str(e)}")
    
    # === Collection Management ===
    
    def get_collections(self) -> List[Dict[str, Any]]:
        """Get all collections with metadata.
        
        Returns:
            List of collection info dicts with metadata
        """
        collections = self.client.list_collections()
        result = []
        
        for collection in collections:
            collection_name = collection.name
            metadata = collection.metadata or {}
            
            # Get collection stats
            count = collection.count()
            
            # Parse JSON strings back to lists
            source_files = metadata.get("source_files", "[]")
            if isinstance(source_files, str):
                try:
                    source_files = json.loads(source_files)
                except (json.JSONDecodeError, TypeError):
                    source_files = []
            
            tags = metadata.get("tags", "[]")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            
            result.append({
                "name": collection_name,
                "count": count,
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
                "description": metadata.get("description", ""),
                "source_type": metadata.get("source_type", "unknown"),
                "source_files": source_files,
                "tags": tags,
            })
        
        return result
    
    def get_collection_metadata(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific collection.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Collection metadata dict or None if not found
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            metadata = collection.metadata or {}
            
            # Parse JSON strings back to lists
            source_files = metadata.get("source_files", "[]")
            if isinstance(source_files, str):
                try:
                    source_files = json.loads(source_files)
                except (json.JSONDecodeError, TypeError):
                    source_files = []
            
            tags = metadata.get("tags", "[]")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            
            return {
                "name": collection_name,
                "count": collection.count(),
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
                "description": metadata.get("description", ""),
                "source_type": metadata.get("source_type", "unknown"),
                "source_files": source_files,
                "tags": tags,
                "chromadb_metadata": metadata,
            }
        except Exception:
            return None
    
    def create_collection(
        self,
        name: str,
        description: str = "",
        source_type: str = "document",
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Create a new collection.
        
        Args:
            name: Collection name
            description: Human-readable description
            source_type: Type of source (document, web, custom, etc.)
            tags: Optional tags for categorization
            
        Returns:
            Result dict with status and collection info
        """
        try:
            # Check if collection already exists
            try:
                self.client.get_collection(name=name)
                return {
                    "status": "error",
                    "message": f"Collection '{name}' already exists"
                }
            except Exception:
                pass  # Collection doesn't exist, proceed
            
            # Create collection with full metadata
            # Note: ChromaDB only accepts scalar values in metadata, so store lists as JSON strings
            now = datetime.now().isoformat()
            collection = self.client.create_collection(
                name=name,
                metadata={
                    "description": description,
                    "created_at": now,
                    "updated_at": now,
                    "source_type": source_type,
                    "source_files": json.dumps([]),
                    "tags": json.dumps(tags or []),
                }
            )
            
            return {
                "status": "success",
                "collection": {
                    "name": name,
                    "description": description,
                    "created_at": now,
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def update_collection_metadata(
        self,
        name: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Update collection metadata.
        
        Args:
            name: Collection name
            description: New description (optional)
            tags: New tags (optional)
            source_files: New source files list (optional)
            
        Returns:
            Result dict with status
        """
        try:
            # Get collection
            collection = self.client.get_collection(name=name)
            current_metadata = collection.metadata or {}
            
            # Update only provided fields
            updated_metadata = dict(current_metadata)
            if description is not None:
                updated_metadata["description"] = description
            if tags is not None:
                # Store list as JSON string
                updated_metadata["tags"] = json.dumps(tags)
            if source_files is not None:
                # Store list as JSON string
                updated_metadata["source_files"] = json.dumps(source_files)
            
            updated_metadata["updated_at"] = datetime.now().isoformat()
            
            # Modify collection metadata
            collection.modify(metadata=updated_metadata)
            
            return {"status": "success", "name": name}
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def delete_collection(self, name: str) -> Dict[str, Any]:
        """Delete a collection.
        
        Args:
            name: Collection name
            
        Returns:
            Result dict with status
        """
        try:
            self.client.delete_collection(name=name)
            
            return {
                "status": "success",
                "name": name,
                "message": f"Collection '{name}' deleted successfully"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    # === Document Management ===
    
    def get_collection_documents(
        self,
        collection_name: str,
        limit: Optional[int] = None,
        offset: int = 0,
        include_embeddings: bool = False
    ) -> Dict[str, Any]:
        """Get all documents from a collection.
        
        Args:
            collection_name: Name of the collection
            limit: Maximum number of documents to return (None = all)
            offset: Number of documents to skip
            include_embeddings: Whether to include embedding vectors (memory intensive)
            
        Returns:
            Dict with status and documents list
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            
            # Get all documents
            results = collection.get(
                limit=limit,
                offset=offset,
                include=["documents", "metadatas", "embeddings"] if include_embeddings else ["documents", "metadatas"]
            )
            
            documents = []
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            embeddings = results.get("embeddings", []) if include_embeddings else []
            
            for i, doc_id in enumerate(ids):
                doc_dict = {
                    "id": doc_id,
                    "document": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                }
                if include_embeddings and i < len(embeddings):
                    doc_dict["embedding"] = embeddings[i]
                documents.append(doc_dict)
            
            return {
                "status": "success",
                "collection": collection_name,
                "documents": documents,
                "total_count": len(documents),
                "offset": offset,
                "limit": limit
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def get_document(
        self,
        collection_name: str,
        document_id: str,
        include_embedding: bool = False
    ) -> Dict[str, Any]:
        """Get a specific document by ID from a collection.
        
        Args:
            collection_name: Name of the collection
            document_id: ID of the document to retrieve
            include_embedding: Whether to include embedding vector (memory intensive)
            
        Returns:
            Dict with status and document info
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            
            # Get specific document by ID
            results = collection.get(
                ids=[document_id],
                include=["documents", "metadatas", "embeddings"] if include_embedding else ["documents", "metadatas"]
            )
            
            ids = results.get("ids", [])
            if not ids or document_id not in ids:
                return {
                    "status": "error",
                    "message": f"Document '{document_id}' not found in collection '{collection_name}'"
                }
            
            idx = ids.index(document_id)
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            embeddings = results.get("embeddings", []) if include_embedding else []
            
            document = {
                "id": document_id,
                "document": docs[idx] if idx < len(docs) else "",
                "metadata": metas[idx] if idx < len(metas) else {},
            }
            if include_embedding and idx < len(embeddings):
                document["embedding"] = embeddings[idx]
            
            return {
                "status": "success",
                "collection": collection_name,
                "document": document
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def add_documents(
        self,
        collection_name: str,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Add documents to a collection with automatic embedding.
        
        Args:
            collection_name: Name of the collection
            documents: List of text documents to add
            metadatas: Optional metadata for each document
            ids: Optional IDs for documents (auto-generated if not provided)
            
        Returns:
            Result dict with status and count
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            
            # Generate IDs if not provided
            if ids is None:
                import uuid
                ids = [str(uuid.uuid4()) for _ in documents]
            
            # Create embeddings
            embeddings = self._create_embeddings_batch(documents)
            
            # Add to collection
            collection.add(
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            
            # Update metadata timestamp
            current_metadata = collection.metadata or {}
            updated_metadata = dict(current_metadata)
            updated_metadata["updated_at"] = datetime.now().isoformat()
            collection.modify(metadata=updated_metadata)
            
            return {
                "status": "success",
                "collection": collection_name,
                "added_count": len(documents)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    # === Querying ===
    
    def query_collection(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
        where_document: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Query a single collection.
        
        Args:
            collection_name: Name of the collection to query
            query_text: Query text
            n_results: Number of results to return
            where: Metadata filter (optional)
            where_document: Document content filter (optional)
            
        Returns:
            Query results with documents, distances, and metadata
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            
            # Create query embedding
            query_embedding = self._create_embedding(query_text)
            
            # Query collection
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where,
                where_document=where_document
            )
            
            return {
                "status": "success",
                "collection": collection_name,
                "query": query_text,
                "results": {
                    "ids": results["ids"][0] if results["ids"] else [],
                    "documents": results["documents"][0] if results["documents"] else [],
                    "metadatas": results["metadatas"][0] if results["metadatas"] else [],
                    "distances": results["distances"][0] if results["distances"] else [],
                },
                "count": len(results["ids"][0]) if results["ids"] else 0
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def query_multiple_collections(
        self,
        collection_names: List[str],
        query_text: str,
        n_results_per_collection: int = 5,
        where: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Query multiple collections and aggregate results.
        
        Args:
            collection_names: List of collection names to query
            query_text: Query text
            n_results_per_collection: Number of results per collection
            where: Metadata filter (optional)
            
        Returns:
            Aggregated query results from all collections
        """
        try:
            all_results = []
            
            for collection_name in collection_names:
                result = self.query_collection(
                    collection_name=collection_name,
                    query_text=query_text,
                    n_results=n_results_per_collection,
                    where=where
                )
                
                if result["status"] == "success":
                    # Add collection name to each result
                    for i, doc in enumerate(result["results"]["documents"]):
                        all_results.append({
                            "collection": collection_name,
                            "document": doc,
                            "metadata": result["results"]["metadatas"][i] if i < len(result["results"]["metadatas"]) else {},
                            "distance": result["results"]["distances"][i] if i < len(result["results"]["distances"]) else 0,
                            "id": result["results"]["ids"][i] if i < len(result["results"]["ids"]) else ""
                        })
            
            # Sort by distance (lower is better)
            all_results.sort(key=lambda x: x["distance"])
            
            return {
                "status": "success",
                "query": query_text,
                "collections": collection_names,
                "results": all_results,
                "total_count": len(all_results)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    # === Document Chunking Helpers ===
    
    def chunk_text_file(
        self,
        file_path: Union[str, Path],
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """Extract chunks from a text file.
        
        Args:
            file_path: Path to the text file
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters
            
        Returns:
            List of chunk dicts with text and metadata
        """
        if not (TextLoader and RecursiveCharacterTextSplitter):
            raise RuntimeError("LangChain not available. Install with: pip install langchain langchain-community")
        
        try:
            path = Path(file_path)
            
            # Load document
            loader = TextLoader(str(path), encoding="utf-8")
            documents = loader.load()
            
            # Split into chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ". ", " ", ""]
            )
            chunks = splitter.split_documents(documents)
            
            # Convert to our format
            result = []
            for i, chunk in enumerate(chunks):
                result.append({
                    "text": chunk.page_content,
                    "metadata": {
                        "source": path.name,
                        "file_path": str(path),
                        "chunk_index": i,
                        "file_type": "txt",
                        # **chunk.metadata
                    }
                })
            
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to chunk text file: {str(e)}")
    
    def chunk_word_file(
        self,
        file_path: Union[str, Path],
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """Extract chunks from a Word document.
        
        Args:
            file_path: Path to the Word file (.docx)
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters
            
        Returns:
            List of chunk dicts with text and metadata
        """
        if not (Docx2txtLoader and RecursiveCharacterTextSplitter):
            raise RuntimeError("LangChain not available. Install with: pip install langchain langchain-community python-docx")
        
        try:
            path = Path(file_path)
            
            # Load document
            loader = Docx2txtLoader(str(path))
            documents = loader.load()
            
            # Split into chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ". ", " ", ""]
            )
            chunks = splitter.split_documents(documents)
            
            # Convert to our format
            result = []
            for i, chunk in enumerate(chunks):
                result.append({
                    "text": chunk.page_content,
                    "metadata": {
                        "source": path.name,
                        "file_path": str(path),
                        "chunk_index": i,
                        "file_type": "docx",
                        # **chunk.metadata
                    }
                })
            
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to chunk Word file: {str(e)}")
    
    def chunk_pdf_file(
        self,
        file_path: Union[str, Path],
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """Extract chunks from a PDF file.
        
        Args:
            file_path: Path to the PDF file
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters
            
        Returns:
            List of chunk dicts with text and metadata
        """
        if not (PyPDFLoader and RecursiveCharacterTextSplitter):
            raise RuntimeError("LangChain not available. Install with: pip install langchain langchain-community pypdf")
        
        try:
            path = Path(file_path)
            
            # Load document
            loader = PyPDFLoader(str(path))
            documents = loader.load()

            full_text = " ".join([doc.page_content for doc in documents])
            
            # Split into chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ". ", " ", ""]
            )
            chunks = splitter.split_text(full_text)
            
            # Convert to our format
            result = []
            for i, chunk in enumerate(chunks):
                result.append({
                    "text": chunk,
                    "metadata": {
                        "source": path.name,
                        "file_path": str(path),
                        "chunk_index": i,
                        "file_type": "pdf",
                        # "page": chunk.metadata.get("page", 0),
                        # **chunk.metadata
                    }
                })
            
            return result
        except Exception as e:
            raise RuntimeError(f"Failed to chunk PDF file: {str(e)}")
    
    def chunk_document(
        self,
        file_path: Union[str, Path],
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """Automatically chunk a document based on file type.
        
        Args:
            file_path: Path to the document
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters
            
        Returns:
            List of chunk dicts with text and metadata
        """
        path = Path(file_path)
        extension = path.suffix.lower()
        
        if extension == ".txt":
            return self.chunk_text_file(path, chunk_size, chunk_overlap)
        elif extension == ".docx" or extension == ".doc":
            return self.chunk_word_file(path, chunk_size, chunk_overlap)
        elif extension == ".pdf":
            return self.chunk_pdf_file(path, chunk_size, chunk_overlap)
        else:
            raise ValueError(f"Unsupported file type: {extension}")
    
    # === Utility Methods ===
    
    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """Get detailed statistics for a collection.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Statistics dict
        """
        try:
            collection = self.client.get_collection(name=collection_name)
            metadata = collection.metadata or {}
            
            return {
                "status": "success",
                "name": collection_name,
                "count": collection.count(),
                "metadata": metadata,
                "chromadb_metadata": metadata
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def reset_database(self) -> Dict[str, Any]:
        """Reset the entire database (WARNING: Deletes all collections).
        
        Returns:
            Result dict with status
        """
        try:
            self.client.reset()
            
            return {
                "status": "success",
                "message": "Database reset successfully"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
