package com.example.notes.link

class Navigator(private val launcher: Launcher) {
    fun navigate(route: Route) {
        when (route) {
            is Route.NoteDetail -> launcher.openNoteDetail(route.id)
            Route.Home -> launcher.openHome()
            Route.NoOp -> Unit
        }
    }
}
