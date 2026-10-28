package com.example.notes.link

sealed class Route {
    data class NoteDetail(val id: Long) : Route()
    object Home : Route()
    object NoOp : Route()
}
