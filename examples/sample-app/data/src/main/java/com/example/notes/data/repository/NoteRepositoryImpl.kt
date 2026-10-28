package com.example.notes.data.repository

import com.example.notes.data.source.NoteDataSource
import com.example.notes.domain.model.Note
import com.example.notes.domain.repository.NoteRepository

class NoteRepositoryImpl @Inject constructor(
    private val source: NoteDataSource,
) : NoteRepository {
    override suspend fun notes(): List<Note> =
        source.list().body().orEmpty().map { Note(it.id, it.title) }

    override suspend fun note(id: Long): Note? =
        source.get(id).body()?.let { Note(it.id, it.title) }
}
