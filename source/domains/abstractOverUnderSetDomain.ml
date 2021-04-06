(*
 * Copyright (c) Facebook, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 *)

(* Implements an abstract set that computes both an over- and an under-approximation. The
   under-approximation comes into play at joins, where the under-approximations are intersected. *)

open AbstractDomainCore

type 'a approximation = {
  element: 'a;
  in_under: bool;
}

module type S = sig
  include AbstractSetDomain.S

  type _ AbstractDomainCore.part +=
    | ElementAndUnder : element approximation AbstractDomainCore.part
    | SetAndUnder : element approximation list AbstractDomainCore.part

  val empty : t

  val inject : element -> element approximation

  val to_approximation : t -> element approximation list

  val of_approximation : element approximation list -> t

  val add_set : t -> to_add:t -> t

  val sequence_join : t -> t -> t
end

module Make (Element : AbstractSetDomain.ELEMENT) = struct
  module Set = Set.Make (Element)

  type biset =
    | Bottom
    | BiSet of {
        over: Set.t;
        under: Set.t;
      }

  module rec Base : (BASE with type t := biset) = MakeBase (Domain)

  and Domain : (S with type t = biset and type element = Set.elt) = struct
    (* Implicitly under <= over at all times. Note that Bottom is different from ({}, {}) since ({},
       {}) is not less or equal to e.g. ({a}, {a}) *)
    type t = biset

    type element = Element.t

    type _ part +=
      | Self : t part
      | Element : Element.t part
      | ElementAndUnder : Element.t approximation part
      | Set : Element.t list part
      | SetAndUnder : Element.t approximation list part

    let bottom = Bottom

    let empty = BiSet { over = Set.empty; under = Set.empty }

    let is_bottom biset = biset = Bottom

    let inject element = { element; in_under = true }

    let element_of ~under element = { element; in_under = Set.mem element under }

    let elements = function
      | Bottom -> []
      | BiSet { over; _ } -> Set.elements over


    let to_approximation = function
      | Bottom -> []
      | BiSet { over; under } ->
          let element_of = element_of ~under in
          Set.elements over |> ListLabels.map ~f:element_of


    let show_approximation { element; in_under } =
      let element_value = Element.show element in
      if in_under then
        Format.sprintf "%s" element_value
      else
        Format.sprintf "%s(-)" element_value


    let show set =
      match set with
      | Bottom -> "<bottom>"
      | _ ->
          to_approximation set
          |> ListLabels.map ~f:show_approximation
          |> String.concat ", "
          |> Format.sprintf "[%s]"


    let pp formatter set = Format.fprintf formatter "%s" (show set)

    let join left right =
      if left == right then
        left
      else
        match left, right with
        | Bottom, _ -> right
        | _, Bottom -> left
        | ( BiSet { over = left_over; under = left_under },
            BiSet { over = right_over; under = right_under } ) ->
            BiSet
              { over = Set.union left_over right_over; under = Set.inter left_under right_under }


    let make ~old ~over ~under =
      match old with
      | BiSet { over = old_over; under = old_under } ->
          if old_over == over && old_under == under then
            old
          else
            BiSet { over; under }
      | Bottom -> BiSet { over; under }


    (* Add every element of ~to_add to the existing set. The element is present in the under
       approximation if it is present in either. *)
    let add_set set ~to_add =
      match set, to_add with
      | Bottom, _ -> to_add
      | _, Bottom -> set
      | BiSet { over; under }, BiSet { over = to_add_over; under = to_add_under } ->
          let over = Set.union over to_add_over in
          let under = Set.union under to_add_under in
          make ~old:set ~over ~under


    (* Logically, this is a union with point-wise meet of whether the element is in the
       under-approximation *)
    let add_element set { element; in_under } =
      match set with
      | Bottom ->
          let singleton = Set.singleton element in
          let under =
            if in_under then
              singleton
            else
              Set.empty
          in
          BiSet { over = singleton; under }
      | BiSet { over; under } ->
          let over = Set.add element over in
          let under =
            if in_under then
              Set.add element under
            else
              under
          in
          make ~old:set ~over ~under


    let add set element = add_element set { element; in_under = true }

    let of_list elements = ListLabels.fold_left ~f:add elements ~init:bottom

    let of_approximation elements = ListLabels.fold_left ~f:add_element ~init:empty elements

    let widen ~iteration:_ ~prev ~next = join prev next

    let less_or_equal ~left ~right =
      if left == right then
        true
      else
        match left, right with
        | Bottom, _ -> true
        | _, Bottom -> false
        | ( BiSet { over = left_over; under = left_under },
            BiSet { over = right_over; under = right_under } ) ->
            if left_over == right_over && left_under == right_under then
              true
            else
              Set.subset left_over right_over && Set.subset right_under left_under


    let subtract to_remove ~from =
      if to_remove == from then
        bottom
      else
        match from, to_remove with
        | Bottom, _ -> Bottom
        | _, Bottom -> from
        | BiSet { over; under }, BiSet { over = remove_over; under = remove_under } ->
            let over = Set.diff over remove_over in
            if Set.is_empty over && Set.equal under remove_under then
              Bottom
            else
              make ~old:from ~over:(Set.union over under) ~under


    let transform_new : type a f. a part -> (transform2, a, f, t, t) operation -> f:f -> t -> t =
     fun part op ~f set ->
      match part, op, set with
      | Element, OpMap, Bottom -> set
      | Element, OpMap, BiSet _ ->
          to_approximation set
          |> ListLabels.map ~f:(fun e -> { e with element = f e.element })
          |> of_approximation
      | Element, OpAdd, _ -> add set f
      | Element, OpFilter, Bottom -> set
      | Element, OpFilter, BiSet { over; under } ->
          let over = Set.filter f over in
          let under = Set.filter f under in
          make ~old:set ~over ~under
      | ElementAndUnder, OpMap, Bottom -> set
      | ElementAndUnder, OpMap, BiSet _ ->
          to_approximation set |> ListLabels.map ~f |> of_approximation
      | ElementAndUnder, OpAdd, _ -> add_element set f
      | ElementAndUnder, OpFilter, Bottom -> Bottom
      | ElementAndUnder, OpFilter, BiSet _ ->
          to_approximation set |> ListLabels.filter ~f |> of_approximation
      | Set, OpMap, _ -> of_list (f (elements set))
      | Set, OpAdd, _ -> ListLabels.fold_left f ~f:add ~init:set
      | Set, OpFilter, _ ->
          if f (elements set) then
            set
          else
            bottom
      | SetAndUnder, OpMap, set -> of_approximation (f (to_approximation set))
      | SetAndUnder, OpAdd, _ ->
          let to_add = of_approximation f in
          add_set set ~to_add
      | SetAndUnder, OpFilter, Bottom -> set
      | SetAndUnder, OpFilter, _ ->
          if f (to_approximation set) then
            set
          else
            bottom
      | Self, OpAdd, _ ->
          (* Special handling of Add here, as we don't want to use the join of the common
             implementation. *)
          add_set set ~to_add:f
      | _ -> Base.transform part op ~f set


    let reduce
        : type a f b. a part -> using:(reduce, a, f, t, b) operation -> f:f -> init:b -> t -> b
      =
     fun part ~using:op ~f ~init set ->
      match part, op, set with
      | Element, OpAcc, Bottom -> init
      | Element, OpAcc, BiSet { over; _ } -> Set.fold f over init
      | Element, OpExists, Bottom -> init
      | Element, OpExists, BiSet { over; _ } -> init || Set.exists f over
      | Set, OpAcc, _ -> f (elements set) init
      | Set, OpExists, _ -> init || f (elements set)
      | ElementAndUnder, OpAcc, Bottom -> init
      | ElementAndUnder, OpAcc, BiSet { over; under } ->
          let element_of = element_of ~under in
          let f element accumulator = f (element_of element) accumulator in
          Set.fold f over init
      | ElementAndUnder, OpExists, Bottom -> init
      | ElementAndUnder, OpExists, BiSet { over; under } ->
          init
          ||
          let element_of = element_of ~under in
          let f element = f (element_of element) in
          Set.exists f over
      | SetAndUnder, OpAcc, set -> f (to_approximation set) init
      | SetAndUnder, OpExists, set -> init || f (to_approximation set)
      | _ -> Base.reduce part ~using:op ~f ~init set


    let partition_new
        : type a f b.
          a part -> (partition, a, f, t, b) operation -> f:f -> t -> (b, t) Core_kernel.Map.Poly.t
      =
     fun part op ~f set ->
      let update_element element = function
        | None -> add_element bottom element
        | Some set -> add_element set element
      in
      match part, op, set with
      | (Element | ElementAndUnder), OpBy, Bottom -> Core_kernel.Map.Poly.empty
      | (Element | ElementAndUnder), OpByFilter, Bottom -> Core_kernel.Map.Poly.empty
      | Element, OpBy, _ ->
          let f result element =
            let key = f element.element in
            Core_kernel.Map.Poly.update result key ~f:(update_element element)
          in
          to_approximation set |> ListLabels.fold_left ~f ~init:Core_kernel.Map.Poly.empty
      | ElementAndUnder, OpBy, BiSet { over; under } ->
          let element_of = element_of ~under in
          let f element result =
            let element = element_of element in
            let key = f element in
            Core_kernel.Map.Poly.update result key ~f:(update_element element)
          in
          Set.fold f over Core_kernel.Map.Poly.empty
      | Set, OpBy, _ ->
          let key = f (elements set) in
          Core_kernel.Map.Poly.singleton key set
      | SetAndUnder, OpBy, _ ->
          let key = f (to_approximation set) in
          Core_kernel.Map.Poly.singleton key set
      | Element, OpByFilter, _ ->
          let f result element =
            match f element.element with
            | Some key -> Core_kernel.Map.Poly.update result key ~f:(update_element element)
            | None -> result
          in
          to_approximation set |> ListLabels.fold_left ~f ~init:Core_kernel.Map.Poly.empty
      | ElementAndUnder, OpByFilter, BiSet { over; under } ->
          let element_of = element_of ~under in
          let f element result =
            let element = element_of element in
            match f element with
            | Some key -> Core_kernel.Map.Poly.update result key ~f:(update_element element)
            | None -> result
          in
          Set.fold f over Core_kernel.Map.Poly.empty
      | Set, OpByFilter, _ -> (
          match f (elements set) with
          | None -> Core_kernel.Map.Poly.empty
          | Some key -> Core_kernel.Map.Poly.singleton key set )
      | SetAndUnder, OpByFilter, _ -> (
          match f (to_approximation set) with
          | None -> Core_kernel.Map.Poly.empty
          | Some key -> Core_kernel.Map.Poly.singleton key set )
      | _ -> Base.partition part op ~f set


    let introspect (type a) (op : a introspect) : a =
      match op with
      | GetParts f ->
          f#report Self;
          f#report Element;
          f#report ElementAndUnder;
          f#report Set;
          f#report SetAndUnder
      | Structure -> [Format.sprintf "OverAndUnderSet(%s)" Element.name]
      | Name part -> (
          match part with
          | Element -> Format.sprintf "OverAndUnderSet(%s).Element" Element.name
          | Set -> Format.sprintf "OverAndUnderSet(%s).Set" Element.name
          | ElementAndUnder -> Format.sprintf "OverAndUnderSet(%s).ElementAndUnder" Element.name
          | SetAndUnder -> Format.sprintf "OverAndUnderSet(%s).SetAndUnder" Element.name
          | Self -> Format.sprintf "OverAndUnderSet(%s).Self" Element.name
          | _ -> Base.introspect op )


    let create parts : t =
      let create_part so_far (Part (part, value)) =
        match part with
        | ElementAndUnder -> add_element so_far value
        | Element -> add so_far value
        | Set -> ListLabels.fold_left ~f:add value ~init:so_far
        | SetAndUnder -> ListLabels.fold_left ~f:add_element value ~init:so_far
        | Self -> add_set so_far ~to_add:(value : t)
        | _ -> Base.create part value so_far
      in
      ListLabels.fold_left parts ~f:create_part ~init:bottom


    let sequence_join left right =
      if left == right then
        left
      else
        match left, right with
        | Bottom, _ -> right
        | _, Bottom -> left
        | ( BiSet { over = left_over; under = left_under },
            BiSet { over = right_over; under = right_under } ) ->
            let over = Set.union left_over right_over in
            let under = Set.union left_under right_under in
            make ~old:left ~over ~under


    let singleton element =
      let singleton = Set.singleton element in
      BiSet { over = singleton; under = singleton }


    let remove element = function
      | Bottom -> Bottom
      | BiSet { over; under } ->
          BiSet { over = Set.remove element over; under = Set.remove element under }


    let add element set = add set element

    let meet = Base.meet

    let transform = Base.legacy_transform

    let fold = Base.fold

    let partition = Base.legacy_partition
  end

  let _ = Base.fold (* unused module warning work-around *)

  include Domain
end
