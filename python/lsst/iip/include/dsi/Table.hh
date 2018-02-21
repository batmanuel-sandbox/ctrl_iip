/*
** ++
**  Package:
**	
**
**  Abstract:
**      
**
**  Author:
**      Michael Huffer, SLAC (mehsys@slac.stanford.edu)
**
**  Creation Date:
**	000 - January 09, 2007
**
**  Revision History:
**	None.
**
** --
*/
 
#ifndef DSI_TABLE
#define DSI_TABLE

#include "hash/Table.hh"

#include "dsi/Element.hh"
#include "dsi/List.hh"

namespace DSI {

class Table : public Hash::Table {
public:
  Table(uint32_t stride) : Hash::Table(stride) {}
public:
 ~Table() {}
public:
  List* seek()           {return (List*)Hash::Table::seek();} 
  List* seek(List* list) {return (List*)Hash::Table::seek(list);} 
public:
  Element* lookup(uint64_t key) {return (Element*)Hash::Table::lookup(key);}
  Element* remove(uint64_t key) {return (Element*)Hash::Table::remove(key);}
};

}
 
#endif

