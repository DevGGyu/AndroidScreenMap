package com.example.notes.domain.repository

import com.example.notes.domain.model.Note

interface NoteRepository {
    suspend fun notes(): List<Note>
    suspend fun note(id: Long): Note?
}
