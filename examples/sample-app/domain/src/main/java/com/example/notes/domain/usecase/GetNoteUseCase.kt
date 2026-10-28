package com.example.notes.domain.usecase

import com.example.notes.domain.repository.NoteRepository

class GetNoteUseCase @Inject constructor(private val repo: NoteRepository) {
    suspend operator fun invoke(id: Long) = repo.note(id)
}
