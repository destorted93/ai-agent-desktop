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

try:
    import requests
except ImportError:
    requests = None  # type: ignore

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .secure import get_app_data_dir
from ..services.confluence import (
    create_confluence_client,
    extract_confluence_page_id,
    infer_confluence_base_url_from_page_url,
    looks_like_confluence_page_url,
    fetch_confluence_page_content,
)


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
        embedding_model: str = "text-embedding-3-small",
        *,
        db_dir_name: str = "ai-agent-desktop-chromadb",
        allow_reset: bool = True,
    ):
        """Initialize the vector database manager.

        Args:
            api_key: OpenAI API key for embeddings
            base_url: Custom API base URL
            embedding_model: OpenAI embedding model name
            db_dir_name: Chroma persistent directory name under app-data (keeps features isolated)
            allow_reset: Allow chromadb client reset (nukes collections) for this DB
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

        # Initialize ChromaDB (scoped under app-data)
        dn = str(db_dir_name or "").strip() or "ai-agent-desktop-chromadb"
        # Keep it a directory name (no path traversal)
        for bad in ("/", "\\", ".."):
            dn = dn.replace(bad, "_")
        self.db_path = get_app_data_dir() / dn
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=bool(allow_reset),
            ),
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

    def upsert_embeddings(
        self,
        collection_name: str,
        embeddings: List[List[float]],
        ids: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        documents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Upsert vectors into a collection.

        This is useful for non-document use-cases (e.g. memory indexing) where you may
        want embeddings + metadata without storing raw plaintext in `documents`.
        """
        try:
            collection = self.client.get_collection(name=collection_name)

            kwargs: Dict[str, Any] = {"embeddings": embeddings, "ids": ids}
            if metadatas is not None:
                kwargs["metadatas"] = metadatas
            if documents is not None:
                kwargs["documents"] = documents

            if hasattr(collection, "upsert"):
                collection.upsert(**kwargs)
            else:
                # Fallback: delete + add
                try:
                    collection.delete(ids=ids)
                except Exception:
                    pass
                collection.add(**kwargs)

            # Update metadata timestamp
            current_metadata = collection.metadata or {}
            updated_metadata = dict(current_metadata)
            updated_metadata["updated_at"] = datetime.now().isoformat()
            collection.modify(metadata=updated_metadata)

            return {"status": "success", "collection": collection_name, "upserted_count": len(ids)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def upsert_documents(
        self,
        collection_name: str,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Upsert documents into a collection with automatic embedding."""
        try:
            if ids is None:
                import uuid

                ids = [str(uuid.uuid4()) for _ in documents]

            embeddings = self._create_embeddings_batch(documents)
            return self.upsert_embeddings(
                collection_name=collection_name,
                embeddings=embeddings,
                ids=ids,
                metadatas=metadatas,
                documents=documents,
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_documents(self, collection_name: str, ids: List[str]) -> Dict[str, Any]:
        """Delete documents/vectors by id from a collection."""
        try:
            collection = self.client.get_collection(name=collection_name)
            collection.delete(ids=ids)

            current_metadata = collection.metadata or {}
            updated_metadata = dict(current_metadata)
            updated_metadata["updated_at"] = datetime.now().isoformat()
            collection.modify(metadata=updated_metadata)

            return {"status": "success", "collection": collection_name, "deleted_count": len(ids)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

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
    
    def chunk_confluence_page(
        self,
        page_url: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        token: Optional[str] = None,
        include_child_pages: bool = False,
        keep_markdown_format: bool = True,
        download_attachments: bool = False,
        use_threading: bool = True,
        max_workers: int = 5
    ) -> List[Dict[str, Any]]:
        """Extract chunks from a Confluence page.
        
        Args:
            page_url: Full URL to the Confluence page (e.g., https://confluence.example.com/pages/123456)
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters
            token: Confluence API token (if None, will try to get from secure storage)
            include_child_pages: If True, recursively fetch all child pages
            keep_markdown_format: If True, convert HTML to markdown; otherwise keep as HTML (default: True)
            download_attachments: If True, download referenced attachments (default: False)
            use_threading: If True, use parallel processing for faster downloads (default: True)
            max_workers: Maximum number of parallel threads (default: 5)
            
        Returns:
            List of chunk dicts with text and metadata
        """
        if not RecursiveCharacterTextSplitter:
            raise RuntimeError(
                "Confluence support not available. Install with: "
                "pip install atlassian-python-api markdownify langchain langchain-community"
            )
        
        if download_attachments and not requests:
            raise RuntimeError(
                "Attachment download requires requests library. Install with: pip install requests"
            )
        
        # Infer Confluence base URL from the page URL.
        # Important: Atlassian Cloud requires the '/wiki' context path.
        base_url = infer_confluence_base_url_from_page_url(page_url)
        if not base_url:
            raise ValueError(f"Could not infer Confluence base URL from: {page_url}")

        # Get token from secure storage if not provided.
        # Requirement: token must exist for the inferred Confluence base URL.
        try:
            page_id = extract_confluence_page_id(page_url)
            if not page_id:
                raise ValueError(f"Could not extract page ID from URL: {page_url}")

            # Connect to Confluence (token required; no legacy fallback)
            confluence = create_confluence_client(base_url=base_url, token=token, cloud=True)
            
            # Setup attachments directory if downloads are enabled
            attachments_base_dir = None
            if download_attachments:
                attachments_base_dir = get_app_data_dir() / "confluence_attachments"
                attachments_base_dir.mkdir(parents=True, exist_ok=True)
            
            # Helper function: Download attachments (only those referenced in content)
            def download_page_attachments(page_id: str, page_title: str, content_html: str) -> List[str]:
                """Download only attachments that are actually referenced in the page content."""
                downloaded = []
                if not download_attachments or not attachments_base_dir:
                    return downloaded
                
                try:
                    # Extract referenced attachment filenames from content
                    referenced_files = set()
                    
                    # Pattern 1: ri:filename="..."
                    referenced_files.update(re.findall(r'ri:filename="([^"]+)"', content_html))
                    
                    # Pattern 2: /download/attachments/PAGEID/filename or /download/thumbnails/PAGEID/filename
                    referenced_files.update(re.findall(rf'/download/(?:attachments|thumbnails)/{page_id}/([^"?\s]+)', content_html))
                    
                    if not referenced_files:
                        return downloaded
                    
                    # Get all attachments for the page
                    attachments = confluence.get_attachments_from_content(page_id)
                    if not attachments or 'results' not in attachments:
                        return downloaded
                    
                    # Create attachments subfolder
                    safe_page_title = re.sub(r'[<>:"/\\|?*]', '_', page_title)
                    attachments_dir = attachments_base_dir / f"{safe_page_title}_{page_id}"
                    attachments_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Filter to only referenced attachments
                    attachments_to_download = [
                        att for att in attachments['results']
                        if att.get('title', 'unknown') in referenced_files
                    ]
                    
                    # Helper: Download single attachment
                    def download_single_attachment(attachment: Dict[str, Any]) -> Optional[str]:
                        att_title = attachment.get('title', 'unknown')
                        download_link = attachment['_links']['download']
                        full_url = base_url + download_link
                        
                        try:
                            headers = {'Authorization': f'Bearer {token}'}
                            response = requests.get(full_url, headers=headers, stream=True, timeout=30)
                            
                            if response.status_code == 200:
                                filepath = attachments_dir / att_title
                                with open(filepath, 'wb') as f:
                                    for chunk in response.iter_content(chunk_size=8192):
                                        f.write(chunk)
                                return str(filepath)
                            else:
                                return None
                        except Exception:
                            return None
                    
                    # Download attachments (parallel if threading enabled)
                    if use_threading and len(attachments_to_download) > 1:
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = [executor.submit(download_single_attachment, att) for att in attachments_to_download]
                            for future in as_completed(futures):
                                result = future.result()
                                if result:
                                    downloaded.append(result)
                    else:
                        # Sequential download
                        for attachment in attachments_to_download:
                            att_title = attachment.get('title', 'unknown')
                            
                            # Check if this attachment is referenced in the content
                            if att_title not in referenced_files:
                                continue
                            
                            result = download_single_attachment(attachment)
                            if result:
                                downloaded.append(result)
                                
                except Exception:
                    pass
                
                return downloaded
            
            # Helper function to get all child page IDs recursively
            def get_all_child_page_ids(page_id: str, visited: Optional[set] = None) -> List[str]:
                """Recursively get all child page IDs."""
                if visited is None:
                    visited = set()
                
                if page_id in visited:
                    return []
                
                visited.add(page_id)
                child_ids = [page_id]
                
                try:
                    children = confluence.get_page_child_by_type(page_id, type='page', start=0, limit=500)
                    
                    if isinstance(children, dict) and 'results' in children:
                        children = children['results']
                    
                    if children and isinstance(children, list):
                        for child in children:
                            child_id = child['id']
                            child_ids.extend(get_all_child_page_ids(child_id, visited))
                except Exception:
                    pass
                
                return child_ids
            
            
            # Get page IDs based on flag
            if include_child_pages:
                all_page_ids = get_all_child_page_ids(page_id)
            else:
                all_page_ids = [page_id]
            
            # Remove duplicates
            all_page_ids = list(set(all_page_ids))
            
            # Helper function to process a single page
            def process_page(pid: str) -> List[Dict[str, Any]]:
                """Process a single page and return its chunks."""
                page_chunks = []

                attachment_cb = download_page_attachments if download_attachments else None
                page_data = fetch_confluence_page_content(
                    confluence=confluence,
                    base_url=base_url,
                    page_id=pid,
                    keep_markdown_format=bool(keep_markdown_format),
                    attachment_downloader=attachment_cb,
                )
                if not page_data:
                    return page_chunks

                title = page_data.get('title', 'Untitled')
                content = page_data.get('content', '')
                page_url_link = page_data.get('url', '')
                attachments = page_data.get('attachments', [])
                
                # Split into chunks
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    separators=["\n\n", "\n", ". ", " ", ""]
                )
                chunks = splitter.split_text(content)
                
                # Convert to our format
                for i, chunk in enumerate(chunks):
                    chunk_metadata = {
                        "source": title,
                        "page_id": pid,
                        "page_url": page_url_link,
                        "chunk_index": i,
                        "file_type": "confluence",
                        "base_url": base_url,
                    }
                    
                    # Add attachment info if available
                    if attachments:
                        chunk_metadata["attachments"] = attachments
                        chunk_metadata["attachment_count"] = len(attachments)
                    
                    page_chunks.append({
                        "text": chunk,
                        "metadata": chunk_metadata
                    })
                
                return page_chunks
            
            # Process all pages (parallel if threading enabled)
            all_chunks = []
            if use_threading and len(all_page_ids) > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(process_page, pid) for pid in all_page_ids]
                    for future in as_completed(futures):
                        page_chunks = future.result()
                        all_chunks.extend(page_chunks)
            else:
                # Sequential processing
                for pid in all_page_ids:
                    page_chunks = process_page(pid)
                    all_chunks.extend(page_chunks)
            
            return all_chunks
        except Exception as e:
            raise RuntimeError(f"Failed to chunk Confluence page: {str(e)}")
    
    def chunk_document(
        self,
        file_path: Union[str, Path, Dict[str, Any]],
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """Automatically chunk a document based on file type or URL.

        Supports:
        - local files (.txt/.pdf/.docx)
        - Confluence page URLs
        - Confluence page descriptors (dict) for extra options (e.g. include_child_pages)

        Args:
            file_path: Path/URL, or a dict like {"url": "https://.../pages/123", "include_child_pages": true}
            chunk_size: Maximum chunk size in characters
            chunk_overlap: Overlap between chunks in characters

        Returns:
            List of chunk dicts with text and metadata
        """
        # Dict payload means "URL with flags"
        if isinstance(file_path, dict):
            page_url = str(file_path.get("url") or file_path.get("page_url") or "").strip()
            if not page_url:
                raise ValueError("Confluence source dict is missing 'url'")
            include_child_pages = bool(file_path.get("include_child_pages", False))
            return self.chunk_confluence_page(
                page_url,
                chunk_size,
                chunk_overlap,
                include_child_pages=include_child_pages,
            )

        # String/Path payload: check URL first
        file_path_str = str(file_path)
        if file_path_str.startswith('http://') or file_path_str.startswith('https://'):
            if looks_like_confluence_page_url(file_path_str):
                return self.chunk_confluence_page(file_path_str, chunk_size, chunk_overlap)
            raise ValueError(f"URL provided but not recognized as Confluence page: {file_path_str}")

        # Regular file processing
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
