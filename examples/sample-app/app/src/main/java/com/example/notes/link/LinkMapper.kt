package com.example.notes.link

data class LinkData(val target: String?, val id: Long = 0L)

/** host → target constant → route. The two `when`s are what the map reads. */
class LinkMapper {
    fun createLinkData(host: String, id: Long): LinkData {
        return when (host) {
            LinkConst.HOST_OPEN -> LinkData(target = LinkConst.TARGET_NOTE, id = id)
            else -> LinkData(target = null)
        }
    }

    fun mapFromLinkData(data: LinkData): Route {
        return when (data.target) {
            LinkConst.TARGET_NOTE -> Route.NoteDetail(data.id)
            LinkConst.TARGET_LIST -> Route.Home
            else -> Route.NoOp
        }
    }
}
