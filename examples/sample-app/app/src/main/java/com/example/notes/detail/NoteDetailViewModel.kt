package com.example.notes.detail

import com.example.notes.domain.usecase.GetNoteUseCase

@HiltViewModel
class NoteDetailViewModel @Inject constructor(
    private val getNote: GetNoteUseCase,
) : ViewModel() {
    fun load(id: Long) {
        viewModelScope.launch { getNote(id) }
    }
}
