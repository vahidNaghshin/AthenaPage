import os

from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

Base = declarative_base()


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost/page_embeddings"
)


def get_engine():
    return create_engine(DATABASE_URL)


class Webpage(Base):
    __tablename__ = "webpages"
    
    id = Column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    url = Column(Text, nullable=False, unique=True)
    title = Column(Text)
    description = Column(Text)  # meta description tag
    author = Column(Text)  # webpage author if available
    language = Column(Text)  # detected language
    domain = Column(Text)  # extracted domain name
    favicon_url = Column(Text)  # favicon of the webpage
    screenshot_url = Column(Text)  # optional screenshot
    raw_content = Column(Text)  # original raw HTML/text
    llm_summary = Column(Text)  # LLM generated summary
    word_count = Column(Integer)  # word count of raw content
    is_chunked = Column(Boolean, default=False)
    status = Column(String, default='pending')  # pending, processed, failed
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now())
    last_visited_at = Column(DateTime(timezone=True), default=func.now())
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="webpage", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    
    id = Column(Uuid, primary_key=True, server_default=func.gen_random_uuid())
    webpage_id = Column(Uuid, ForeignKey("webpages.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)  # order of chunk in document
    content = Column(Text, nullable=False)  # actual chunk text
    embedding = Column(Vector(1536))  # OpenAI ada-002 = 1536, 3-small = 1536, 3-large = 3072, Llama = 4096
    token_count = Column(Integer)  # token count of chunk
    chunk_type = Column(String, default='summary')  # summary, raw, heading
    created_at = Column(DateTime(timezone=True), default=func.now())
    
    # Relationship to webpage
    webpage = relationship("Webpage", back_populates="chunks")


def create_tables():
    """Create the tables in the database"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("Tables created successfully!")


def get_db_session():
    """Get a database session"""
    engine = get_engine()
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


if __name__ == "__main__":
    create_tables()
